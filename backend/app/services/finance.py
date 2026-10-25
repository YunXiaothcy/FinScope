import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.money import SUPPORTED_CURRENCIES, ZERO
from app.models import (
    Account,
    AuditLog,
    Category,
    FundsMovement,
    IdempotencyRequest,
    Transaction,
    Transfer,
)
from app.schemas.finance import (
    AccountPublic,
    TransactionCreate,
    TransactionPublic,
    TransferPublic,
)

HK = ZoneInfo("Asia/Hong_Kong")


def begin_write(db: Session) -> None:
    # SQLite 无行级锁，开发环境提前获得写锁；PostgreSQL 使用下面的行级锁。
    if db.bind.dialect.name == "sqlite":
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as exc:
            db.rollback()
            raise HTTPException(409, "数据库忙，请使用相同请求编号重试") from exc


def check_currency(currency: str) -> None:
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(422, "本阶段仅支持 CNY、HKD、USD、EUR 基础货币")


def get_account(
    db: Session, workspace_id: str, account_id: str, active: bool = True
) -> Account:
    account = db.scalar(
        select(Account).where(
            Account.id == account_id, Account.workspace_id == workspace_id
        )
    )
    if account is None:
        raise HTTPException(404, "账户不存在于当前空间")
    if active and not account.is_active:
        raise HTTPException(422, "账户已停用")
    return account


def audit(
    db: Session,
    workspace_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None,
    after: dict | None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )


def balances(db: Session, workspace_id: str) -> list[AccountPublic]:
    accounts = db.scalars(
        select(Account)
        .where(Account.workspace_id == workspace_id)
        .order_by(Account.created_at)
    ).all()
    totals = {account.id: account.opening_balance for account in accounts}
    for account_id, amount in db.execute(
        select(FundsMovement.account_id, FundsMovement.amount).where(
            FundsMovement.workspace_id == workspace_id
        )
    ):
        totals[account_id] += amount
    return [
        AccountPublic(
            id=a.id,
            name=a.name,
            type=a.type,
            currency=a.currency,
            opening_balance=a.opening_balance,
            balance=totals[a.id],
            is_active=a.is_active,
        )
        for a in accounts
    ]


def snapshot(record: Transaction | Transfer) -> dict:
    schema = TransactionPublic if isinstance(record, Transaction) else TransferPublic
    return schema.model_validate(record).model_dump(mode="json")


def idempotent_create[Record](
    db: Session,
    workspace_id: str,
    user_id: str,
    key: str,
    operation: str,
    payload,
    build: Callable[[], Record],
    serializer: Callable[[Record], dict] | None = None,
    context: str | None = None,
) -> dict:
    begin_write(db)
    fingerprint_input = {
        "operation": operation,
        "payload": payload.model_dump(mode="json"),
    }
    if context is not None:
        fingerprint_input["context"] = context
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_input,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def replay():
        existing = db.scalar(
            select(IdempotencyRequest).where(
                IdempotencyRequest.workspace_id == workspace_id,
                IdempotencyRequest.user_id == user_id,
                IdempotencyRequest.key == key,
            )
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise HTTPException(409, "同一请求编号不能用于不同内容")
            return existing.response
        return None

    existing_response = replay()
    if existing_response is not None:
        db.rollback()
        return existing_response
    try:
        record = build()
        db.flush()
        response = serializer(record) if serializer is not None else snapshot(record)
        db.add(
            IdempotencyRequest(
                workspace_id=workspace_id,
                user_id=user_id,
                key=key,
                fingerprint=fingerprint,
                entity_type=operation,
                entity_id=record.id,
                response=response,
            )
        )
        audit(db, workspace_id, user_id, "create", operation, record.id, None, response)
        db.commit()
        return response
    except IntegrityError as exc:
        db.rollback()
        existing_response = replay()
        if existing_response is not None:
            return existing_response
        raise HTTPException(409, "记录冲突，请刷新后重试") from exc


def get_transaction(
    db: Session, workspace_id: str, transaction_id: str, lock: bool = False
) -> Transaction:
    statement = select(Transaction).where(
        Transaction.id == transaction_id, Transaction.workspace_id == workspace_id
    )
    if lock:
        statement = statement.with_for_update()
    record = db.scalar(statement)
    if record is None:
        raise HTTPException(404, "交易不存在于当前空间")
    return record


def has_refunds(db: Session, record: Transaction) -> bool:
    return (
        db.scalar(
            select(Transaction.id)
            .where(
                Transaction.related_transaction_id == record.id,
                Transaction.workspace_id == record.workspace_id,
                Transaction.status == "posted",
            )
            .limit(1)
        )
        is not None
    )


def validate_transaction(
    db: Session, workspace_id: str, payload: TransactionCreate
) -> tuple[Account, str | None, Decimal]:
    account = get_account(db, workspace_id, payload.account_id)
    category_id = payload.category_id
    if payload.type == "refund":
        original = get_transaction(
            db, workspace_id, payload.related_transaction_id, lock=True
        )
        if original.status != "posted" or original.type not in {"income", "expense"}:
            raise HTTPException(422, "只能为有效的收入或支出登记退款")
        if original.source == "settlement":
            raise HTTPException(422, "应收应付结算请撤销后重新结算，不能直接登记退款")
        if account.id != original.account_id:
            raise HTTPException(422, "本阶段退款须退回原账户")
        original_time = original.occurred_at
        if original_time.tzinfo is None:
            original_time = original_time.replace(tzinfo=UTC)
        if payload.occurred_at < original_time:
            raise HTTPException(422, "退款时间不能早于原交易")
        refunded = sum(
            db.scalars(
                select(Transaction.amount).where(
                    Transaction.workspace_id == workspace_id,
                    Transaction.related_transaction_id == original.id,
                    Transaction.status == "posted",
                )
            ).all(),
            ZERO,
        )
        if refunded + payload.amount > original.amount:
            raise HTTPException(422, "退款总额不能超过原交易金额")
        if category_id is not None and category_id != original.category_id:
            raise HTTPException(422, "退款分类须与原交易一致")
        category_id = original.category_id
        effect = payload.amount if original.type == "expense" else -payload.amount
    elif payload.type == "adjustment":
        effect = (
            payload.amount
            if payload.adjustment_direction == "increase"
            else -payload.amount
        )
    else:
        category = db.scalar(
            select(Category).where(
                Category.id == category_id, Category.workspace_id == workspace_id
            )
        )
        if category is None:
            raise HTTPException(404, "分类不存在于当前空间")
        if category.type != payload.type:
            raise HTTPException(422, "分类类型与收入或支出不一致")
        effect = payload.amount if payload.type == "income" else -payload.amount
    return account, category_id, effect


def post_transaction(
    db: Session, workspace_id: str, user_id: str, payload: TransactionCreate
) -> Transaction:
    account, category_id, effect = validate_transaction(db, workspace_id, payload)
    record = Transaction(
        workspace_id=workspace_id,
        created_by=user_id,
        currency=account.currency,
        **payload.model_dump(exclude={"category_id"}),
        category_id=category_id,
    )
    record.occurred_at = payload.occurred_at.astimezone(UTC)
    db.add(record)
    db.flush()
    db.add(
        FundsMovement(
            workspace_id=workspace_id,
            account_id=account.id,
            transaction_id=record.id,
            amount=effect,
        )
    )
    return record


def reverse_movements(db: Session, record: Transaction | Transfer) -> None:
    source = (
        FundsMovement.transaction_id
        if isinstance(record, Transaction)
        else FundsMovement.transfer_id
    )
    entries = db.scalars(
        select(FundsMovement).where(
            source == record.id, FundsMovement.workspace_id == record.workspace_id
        )
    ).all()
    reversed_ids = {entry.reverses_id for entry in entries if entry.reverses_id}
    for entry in entries:
        if entry.reverses_id is None and entry.id not in reversed_ids:
            db.add(
                FundsMovement(
                    workspace_id=record.workspace_id,
                    account_id=entry.account_id,
                    transaction_id=entry.transaction_id,
                    transfer_id=entry.transfer_id,
                    amount=-entry.amount,
                    reverses_id=entry.id,
                )
            )


def period_bounds(
    start: date | None, end: date | None
) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(HK).date()
    start = start or today.replace(day=1)
    end = end or today
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if end == date.max:
        raise HTTPException(422, "结束日期超出允许范围")
    return (
        start,
        end,
        datetime.combine(start, time.min, HK).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, HK).astimezone(UTC),
    )
