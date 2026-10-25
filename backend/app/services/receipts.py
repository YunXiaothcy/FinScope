from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attachment, Workspace
from app.ocr.extractors import redact_text
from app.schemas.receipt import (
    ReceiptConfirm,
    ReceiptPublic,
    ReceiptReview,
    ReceiptSummary,
)
from app.security.limits import WindowLimiter
from app.services.business import aware
from app.services.finance import HK, audit, post_transaction, snapshot
from app.services.import_batch import lock_workspace
from app.services.receipt_images import SafeImage

_slots = BoundedSemaphore(2)
_attempts = WindowLimiter(5, 60)


@contextmanager
def processing_slot(user_id: str):
    _attempts.consume(user_id)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "处理任务繁忙，请稍后重试")
    try:
        yield
    finally:
        _slots.release()


def get_receipt(
    db: Session, workspace_id: str, receipt_id: str, lock=False
) -> Attachment:
    statement = (
        select(Attachment)
        .where(Attachment.id == receipt_id, Attachment.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    record = db.scalar(statement.with_for_update() if lock else statement)
    if record is None:
        raise HTTPException(404, "票据不存在于当前空间")
    return record


def purge_expired(db: Session, workspace_id: str):
    for receipt in db.scalars(
        select(Attachment).where(
            Attachment.workspace_id == workspace_id,
            Attachment.expires_at <= datetime.now(UTC),
        )
    ):
        receipt.image_data, receipt.ocr_text = None, None
        if receipt.status == "draft":
            receipt.status = "expired"
            receipt.candidates = None
            receipt.reviewed_payload = None
    db.flush()


def ensure_draft(receipt: Attachment, version: int):
    if receipt.status != "draft" or aware(receipt.expires_at) <= datetime.now(UTC):
        raise HTTPException(409, "票据已确认、清理或过期，请查看票据列表")
    if receipt.version != version:
        raise HTTPException(409, "票据已被修改，请重新打开后核对")


def summary(receipt: Attachment) -> ReceiptSummary:
    return ReceiptSummary(
        id=receipt.id,
        filename=receipt.filename,
        status=receipt.status,
        ocr_status=receipt.ocr_status,
        version=receipt.version,
        created_at=aware(receipt.created_at),
        expires_at=aware(receipt.expires_at),
        transaction_id=receipt.transaction_id,
    )


def public(receipt: Attachment) -> ReceiptPublic:
    return ReceiptPublic(
        **summary(receipt).model_dump(),
        media_type=receipt.media_type,
        byte_size=receipt.byte_size,
        width=receipt.width,
        height=receipt.height,
        image_available=receipt.status in {"draft", "confirmed"}
        and aware(receipt.expires_at) > datetime.now(UTC),
        ocr_provider=receipt.ocr_provider,
        ocr_text=receipt.ocr_text,
        extraction_provider=receipt.extraction_provider,
        candidates=receipt.candidates,
        reviewed_payload=receipt.reviewed_payload,
        notices=receipt.notices,
        external_text_preview=redact_text(receipt.ocr_text or ""),
    )


def create(
    db: Session, workspace_id: str, user_id: str, filename: str, image: SafeImage
) -> Attachment:
    lock_workspace(db, workspace_id)
    purge_expired(db, workspace_id)
    existing = db.scalar(
        select(Attachment)
        .where(
            Attachment.workspace_id == workspace_id,
            Attachment.image_hash == image.image_hash,
            Attachment.status.in_(["draft", "confirmed"]),
        )
        .order_by(Attachment.created_at.desc())
    )
    if existing is not None:
        return existing
    pending = list(
        db.scalars(
            select(Attachment.id).where(
                Attachment.workspace_id == workspace_id, Attachment.status == "draft"
            )
        )
    )
    if len(pending) >= 20:
        raise HTTPException(422, "最多暂存 20 张待处理票据，请先确认或清理")
    receipt = Attachment(
        workspace_id=workspace_id,
        filename=filename,
        image_hash=image.image_hash,
        image_data=image.data,
        byte_size=len(image.data),
        width=image.width,
        height=image.height,
        created_by=user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    db.add(receipt)
    db.flush()
    audit(
        db,
        workspace_id,
        user_id,
        "upload",
        "receipt",
        receipt.id,
        None,
        {"width": image.width, "height": image.height, "bytes": len(image.data)},
    )
    return receipt


def validate_review(workspace: Workspace, receipt: Attachment, payload: ReceiptReview):
    if payload.transaction.type not in {"income", "expense"}:
        raise HTTPException(422, "票据入口仅支持收入或支出，其他业务请使用原账务功能")
    if not 1970 <= payload.transaction.occurred_at.astimezone(HK).year <= 2100:
        raise HTTPException(422, "票据日期须在 1970—2100 年之间")
    if payload.currency != workspace.base_currency:
        raise HTTPException(422, "入账币种必须与空间一致，不做汇率转换")
    currency_hint = (receipt.candidates or {}).get("currency")
    if (
        currency_hint
        and currency_hint != workspace.base_currency
        and not payload.currency_hint_corrected
    ):
        raise HTTPException(
            422, "候选币种与空间不符；请切换空间，或核对图片后明确修正误识别"
        )


def confirm(
    db: Session,
    workspace: Workspace,
    receipt_id: str,
    user_id: str,
    payload: ReceiptConfirm,
) -> Attachment:
    lock_workspace(db, workspace.id)
    receipt = get_receipt(db, workspace.id, receipt_id, lock=True)
    ensure_draft(receipt, payload.version)
    if not receipt.reviewed_payload:
        raise HTTPException(422, "请先保存人工核对的交易草稿")
    reviewed = ReceiptReview(version=receipt.version, **receipt.reviewed_payload)
    validate_review(workspace, receipt, reviewed)
    transaction_input = reviewed.transaction
    transaction = post_transaction(db, workspace.id, user_id, transaction_input)
    transaction.source = "ocr"
    receipt.transaction_id, receipt.status = transaction.id, "confirmed"
    receipt.version += 1
    db.flush()
    audit(
        db,
        workspace.id,
        user_id,
        "create",
        "receipt_transaction",
        transaction.id,
        None,
        {**snapshot(transaction), "attachment_id": receipt.id, "human_reviewed": True},
    )
    return receipt
