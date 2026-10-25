import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.money import CENT, validate_money
from app.models import Category, ImportBatch, ImportRow, Transaction, Workspace
from app.schemas.finance import TransactionCreate
from app.schemas.import_batch import (
    ConfirmRequest,
    ImportMapping,
    ImportPublic,
    ImportSummary,
)
from app.services.business import aware
from app.services.finance import (
    HK,
    audit,
    get_account,
    post_transaction,
    snapshot,
    validate_transaction,
)
from app.services.import_parser import ParsedFile


def batch_rows(db: Session, batch: ImportBatch) -> list[ImportRow]:
    return list(
        db.scalars(
            select(ImportRow)
            .where(
                ImportRow.batch_id == batch.id,
                ImportRow.workspace_id == batch.workspace_id,
            )
            .order_by(ImportRow.row_number)
        )
    )


def get_batch(db: Session, workspace_id: str, batch_id: str, lock=False) -> ImportBatch:
    query = select(ImportBatch).where(
        ImportBatch.id == batch_id, ImportBatch.workspace_id == workspace_id
    )
    batch = db.scalar(query.with_for_update() if lock else query)
    if batch is None:
        raise HTTPException(404, "导入批次不存在于当前空间")
    return batch


def lock_workspace(db: Session, workspace_id: str):
    # PostgreSQL 将同空间的确认操作串行化；SQLite 由调用者的 BEGIN IMMEDIATE 保护。
    db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())


def purge_expired(db: Session, workspace_id: str):
    now = datetime.now(UTC)
    for batch in db.scalars(
        select(ImportBatch).where(
            ImportBatch.workspace_id == workspace_id, ImportBatch.expires_at <= now
        )
    ):
        if batch.status == "draft":
            batch.status = "expired"
        for row in batch_rows(db, batch):
            row.raw = None
            if row.status != "imported":
                row.normalized = None
            row.parsing_errors = []
    db.flush()


def summary(db: Session, batch: ImportBatch) -> ImportSummary:
    rows = batch_rows(db, batch)
    return ImportSummary(
        id=batch.id,
        filename=batch.filename,
        status=batch.status,
        version=batch.version,
        created_at=aware(batch.created_at),
        expires_at=aware(batch.expires_at),
        total=len(rows),
        counts=dict(Counter(row.status for row in rows)),
    )


def public_batch(db: Session, batch: ImportBatch) -> ImportPublic:
    return ImportPublic(
        **summary(db, batch).model_dump(),
        format=batch.format,
        encoding=batch.encoding,
        sheet_name=batch.sheet_name,
        headers=batch.headers,
        mapping=batch.mapping,
        rows=batch_rows(db, batch),
    )


def create_batch(
    db: Session,
    workspace_id: str,
    user_id: str,
    filename: str,
    data: bytes,
    parsed: ParsedFile,
) -> ImportBatch:
    lock_workspace(db, workspace_id)
    purge_expired(db, workspace_id)
    file_hash = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.workspace_id == workspace_id,
            ImportBatch.file_hash == file_hash,
            ImportBatch.status.in_(["draft", "committed"]),
        )
        .order_by(ImportBatch.created_at.desc())
    )
    if existing is not None:
        return existing
    pending = list(
        db.scalars(
            select(ImportBatch.id).where(
                ImportBatch.workspace_id == workspace_id, ImportBatch.status == "draft"
            )
        )
    )
    if len(pending) >= 20:
        raise HTTPException(422, "最多保留 20 个待处理批次，请先取消或完成旧批次")
    batch = ImportBatch(
        workspace_id=workspace_id,
        created_by=user_id,
        filename=filename,
        file_hash=file_hash,
        format=filename.rsplit(".", 1)[-1].lower(),
        headers=parsed.headers,
        encoding=parsed.encoding,
        sheet_name=parsed.sheet_name,
        expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    db.add(batch)
    db.flush()
    for number, cells, errors in parsed.rows:
        db.add(
            ImportRow(
                workspace_id=workspace_id,
                batch_id=batch.id,
                row_number=number,
                raw=cells,
                parsing_errors=errors,
            )
        )
    audit(
        db,
        workspace_id,
        user_id,
        "upload",
        "import_batch",
        batch.id,
        None,
        {"format": batch.format, "row_count": len(parsed.rows)},
    )
    db.flush()
    return batch


def ensure_draft(batch: ImportBatch, version: int):
    if batch.status != "draft" or aware(batch.expires_at) <= datetime.now(UTC):
        raise HTTPException(409, "批次已完成、取消或过期，请返回导入列表查看")
    if batch.version != version:
        raise HTTPException(409, "批次已被修改，请刷新后重新预览")


def validate_mapping(
    db: Session, workspace_id: str, batch: ImportBatch, mapping: ImportMapping
):
    account = get_account(db, workspace_id, mapping.account_id)
    for key, column in mapping.model_dump().items():
        if (
            key.endswith("_column")
            and column is not None
            and column >= len(batch.headers)
        ):
            raise HTTPException(422, "映射列超出表头范围")
    categories = {
        item.id: item
        for item in db.scalars(
            select(Category).where(Category.workspace_id == workspace_id)
        )
    }
    configured = [
        *mapping.category_map.values(),
        mapping.default_income_category_id,
        mapping.default_expense_category_id,
    ]
    if any(value and value not in categories for value in configured):
        raise HTTPException(404, "映射分类不存在于当前空间")
    for kind in ("income", "expense"):
        category_id = getattr(mapping, f"default_{kind}_category_id")
        if category_id and categories[category_id].type != kind:
            raise HTTPException(422, "默认分类与收支类型不一致")
    return account, categories


def clean(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def normalize_date(raw: str, order: str) -> datetime:
    value = None
    text = clean(raw)
    if order == "ymd":
        try:
            value = datetime.fromisoformat(text.replace("/", "-"))
        except ValueError:
            pass
    if value is None:
        date_format = {"ymd": "%Y-%m-%d", "dmy": "%d-%m-%Y", "mdy": "%m-%d-%Y"}[order]
        for suffix in ("", " %H:%M", " %H:%M:%S"):
            try:
                value = datetime.strptime(
                    text.replace("/", "-").replace("T", " "), date_format + suffix
                )
                break
            except ValueError:
                continue
    if value is None or not 1970 <= value.year <= 2100:
        raise ValueError("日期无效，请选择正确的年月日顺序；支持 1970—2100 年")
    return (value.replace(tzinfo=HK) if value.tzinfo is None else value).astimezone(UTC)


def normalize_amount(raw: str) -> str:
    text = clean(raw)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    if not re.fullmatch(r"\d{1,16}(?:\.\d{1,16})?(?:[Ee][+-]?\d{1,3})?", text):
        raise ValueError(
            "金额必须为正数，最多 16 位整数和 2 位有效小数；不要包含货币符号"
        )
    try:
        value = Decimal(text)
        if value <= 0 or value.adjusted() > 15 or value != value.quantize(CENT):
            raise ValueError(
                "金额须大于零，最多 16 位整数和 2 位有效小数；负数/退款请手工处理"
            )
        return str(validate_money(format(value.quantize(CENT), "f")))
    except InvalidOperation as exc:
        raise ValueError("金额精度无效") from exc


def fingerprint(payload: dict, source_name: str, external_id: str = "") -> str:
    if external_id:
        content = {
            "account": payload["account_id"],
            "source": source_name,
            "external_id": external_id,
        }
    else:
        content = {
            key: payload[key]
            for key in ("account_id", "type", "amount", "description", "merchant_name")
        }
        content["occurred_at"] = (
            datetime.fromisoformat(payload["occurred_at"])
            .astimezone(UTC)
            .isoformat(timespec="microseconds")
        )
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def normalize_row(
    row: ImportRow, mapping: ImportMapping, currency: str, categories: dict
) -> dict:
    if row.parsing_errors:
        raise ValueError("；".join(row.parsing_errors))
    if row.raw is None:
        raise ValueError("原始预览数据已清理，请重新上传")

    def cell(column):
        return (
            clean(row.raw[column])
            if column is not None and column < len(row.raw)
            else ""
        )

    label = cell(mapping.type_column)
    if mapping.type_column is None:
        kind = mapping.fixed_type
    else:
        if any(
            word in label.casefold()
            for word in (
                "退款",
                "转账",
                "借款",
                "还款",
                "结算",
                "应收",
                "应付",
                "refund",
                "transfer",
                "adjustment",
            )
        ):
            raise ValueError("退款、转账、借还款和应收应付结算请通过对应账务功能处理")
        types = {
            "收入": "income",
            "支出": "expense",
            "收款": "income",
            "付款": "expense",
            "income": "income",
            "expense": "expense",
            **mapping.type_map,
        }
        kind = types.get(label, types.get(label.casefold()))
        if kind is None:
            raise ValueError("收支类型无法识别，请配置类型映射；不支持自动推断正负金额")
    source_category = cell(mapping.category_column)
    category_id = (
        mapping.category_map.get(source_category)
        if source_category
        else getattr(mapping, f"default_{kind}_category_id")
    )
    if not category_id:
        raise ValueError("该行分类未映射，请选择来源分类对应关系或默认收支分类")
    category = categories.get(category_id)
    if category is None or category.type != kind:
        raise ValueError("该行分类与收支类型不一致")
    if (
        cell(mapping.currency_column)
        and cell(mapping.currency_column).upper() != currency
    ):
        raise ValueError("该行币种与空间基础货币不一致，本阶段不做汇率换算")
    external_id = cell(mapping.external_id_column)
    if len(external_id) > 120:
        raise ValueError("来源单号最多 120 字")
    try:
        payload = TransactionCreate(
            account_id=mapping.account_id,
            category_id=category_id,
            type=kind,
            amount=normalize_amount(cell(mapping.amount_column)),
            occurred_at=normalize_date(cell(mapping.date_column), mapping.date_format),
            description=cell(mapping.description_column),
            merchant_name=cell(mapping.merchant_column),
        )
    except ValidationError as exc:
        raise ValueError(
            "交易字段无效：备注最多 500 字，商户最多 120 字，金额和日期须符合格式"
        ) from exc
    result = payload.model_dump(mode="json")
    return {
        **result,
        "external_id": external_id,
        "category_name": category.name,
        "fingerprint": fingerprint(result, mapping.source_name, external_id),
    }


def existing_fingerprints(db: Session, workspace_id: str):
    imported = set()
    for row in db.scalars(
        select(ImportRow).where(
            ImportRow.workspace_id == workspace_id,
            ImportRow.fingerprint.is_not(None),
        )
    ):
        imported.add(row.fingerprint)
        imported.add(f"any:{row.content_fingerprint}")
        if not row.normalized.get("external_id"):
            imported.add(f"anonymous:{row.content_fingerprint}")
    manual = set()
    for transaction in db.scalars(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "import",
            Transaction.status == "posted",
            Transaction.type.in_(["income", "expense"]),
        )
    ):
        payload = snapshot(transaction)
        manual.add(fingerprint(payload, ""))
    return imported, manual


def duplicate_reason(normalized: dict, imported: set, manual: set, seen: set) -> str:
    key = normalized["fingerprint"]
    content_key = fingerprint(normalized, "")
    fallback_key = (
        f"{'anonymous' if normalized['external_id'] else 'any'}:{content_key}"
    )
    if key in imported:
        return "此前已导入同一记录（撤销/编辑后仍保留来源去重标记）"
    if fallback_key in imported:
        return "此前已导入同样内容；本次或原记录缺少来源单号，请核对"
    if content_key in manual:
        return "账本已有内容相同的交易，请核对"
    if key in seen or fallback_key in seen:
        return "本批次已有相同记录"
    return ""


def remember_seen(seen: set, normalized: dict):
    seen.add(normalized["fingerprint"])
    content_key = fingerprint(normalized, "")
    seen.add(f"any:{content_key}")
    if not normalized["external_id"]:
        seen.add(f"anonymous:{content_key}")


def preview(db: Session, workspace: Workspace, batch: ImportBatch, request):
    ensure_draft(batch, request.version)
    _, categories = validate_mapping(db, workspace.id, batch, request.mapping)
    imported, manual = existing_fingerprints(db, workspace.id)
    seen = set()
    for row in batch_rows(db, batch):
        row.normalized = None
        row.messages = []
        try:
            normalized = normalize_row(
                row, request.mapping, workspace.base_currency, categories
            )
            row.normalized = normalized
            reason = duplicate_reason(normalized, imported, manual, seen)
            row.status = "duplicate" if reason else "ready"
            row.messages = [reason] if reason else []
            remember_seen(seen, normalized)
        except ValueError as exc:
            row.status = "error"
            row.messages = [str(exc)]
    batch.mapping = request.mapping.model_dump()
    # 即使映射相同，重新预览也使旧确认版本失效。
    batch.version += 1
    db.flush()


def confirm(
    db: Session,
    workspace: Workspace,
    batch_id: str,
    user_id: str,
    request: ConfirmRequest,
) -> ImportBatch:
    lock_workspace(db, workspace.id)
    batch = get_batch(db, workspace.id, batch_id, lock=True)
    ensure_draft(batch, request.version)
    if not batch.mapping:
        raise HTTPException(422, "请先完成字段映射和预览")
    mapping = ImportMapping.model_validate(batch.mapping)
    _, categories = validate_mapping(db, workspace.id, batch, mapping)
    rows = batch_rows(db, batch)
    selected = set(request.row_ids)
    if selected - {row.id for row in rows if row.status == "ready"}:
        raise HTTPException(422, "只能确认当前预览中有效且不重复的行")
    imported, manual = existing_fingerprints(db, workspace.id)
    seen = set()
    prepared = []
    # 写入前重新验证全部所选行；任何失效字段使整批回滚。
    for row in rows:
        if row.id not in selected:
            continue
        try:
            normalized = normalize_row(
                row, mapping, workspace.base_currency, categories
            )
        except ValueError as exc:
            raise HTTPException(422, "所选行已失效，请重新预览") from exc
        reason = duplicate_reason(normalized, imported, manual, seen)
        payload = TransactionCreate.model_validate(normalized)
        validate_transaction(db, workspace.id, payload)
        prepared.append((row, normalized, payload, reason))
        remember_seen(seen, normalized)
    for row, normalized, payload, reason in prepared:
        row.normalized = normalized
        if reason:
            row.status, row.messages = "duplicate", [reason]
            continue
        transaction = post_transaction(db, workspace.id, user_id, payload)
        transaction.source = "import"
        row.transaction_id = transaction.id
        row.fingerprint = normalized["fingerprint"]
        row.content_fingerprint = fingerprint(normalized, "")
        row.status, row.messages = "imported", []
        audit(
            db,
            workspace.id,
            user_id,
            "create",
            "import_transaction",
            transaction.id,
            None,
            {
                **snapshot(transaction),
                "batch_id": batch.id,
                "row_number": row.row_number,
            },
        )
    for row in rows:
        if row.id not in selected and row.status == "ready":
            row.status = "skipped"
            row.messages = ["用户未选择此行"]
    batch.status = "committed"
    batch.version += 1
    db.flush()
    return batch
