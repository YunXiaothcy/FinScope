import hashlib
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.v1.finance import write_access
from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.session import get_db
from app.models import Attachment
from app.ocr.extractors import (
    CompatibleReceiptExtractor,
    ExtractionUnavailable,
    RuleReceiptExtractor,
    ai_configuration,
    redact_text,
)
from app.ocr.providers import OcrProvider, OcrUnavailable, get_ocr_provider
from app.ocr.sample import sample_receipt
from app.schemas.finance import VersionRequest
from app.schemas.receipt import (
    ReceiptConfirm,
    ReceiptPublic,
    ReceiptReview,
    ReceiptSummary,
    StructureRequest,
)
from app.security.access import revalidate_write_access
from app.security.auth import AuthContext, require_csrf
from app.services import receipts as service
from app.services.finance import (
    audit,
    begin_write,
    idempotent_create,
    validate_transaction,
)
from app.services.import_batch import lock_workspace
from app.services.receipt_images import MAX_IMAGE_BYTES, sanitize_image

router = APIRouter(prefix="/workspaces/{workspace_id}/receipts", tags=["receipts"])
IdempotencyKey = Annotated[
    str, Header(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
]


def maintenance(db: Session, workspace_id: str):
    begin_write(db)
    lock_workspace(db, workspace_id)
    service.purge_expired(db, workspace_id)


def get_ai_extractor():
    return CompatibleReceiptExtractor(ai_configuration())


@router.get("/capabilities")
def capabilities(
    response: Response, access: WorkspaceAccess = Depends(get_workspace_access)
):
    config = ai_configuration()
    response.headers["Cache-Control"] = "no-store"
    return {
        "ai_enabled": config["enabled"],
        "ai_host": config["host"] if config["enabled"] else "",
        "ai_model": config["model"] if config["enabled"] else "",
        "ai_target": f"{config['base']} / {config['model']}"
        if config["enabled"]
        else "",
        "max_bytes": MAX_IMAGE_BYTES,
        "retention_hours": 72,
        "disclosure": "云端只发送下面预览的识别文字，不发送图片、账户或工作空间信息。自动脱敏不保证覆盖所有敏感信息，请核对后授权。",
    }


@router.post("", response_model=ReceiptPublic, status_code=201)
async def upload(
    request: Request,
    response: Response,
    filename: str = Header(alias="X-File-Name", min_length=1, max_length=2000),
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    try:
        filename = unquote(filename, errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "文件名编码无效") from exc
    if (
        request.headers.get("content-type", "").split(";")[0]
        != "application/octet-stream"
    ):
        raise HTTPException(415, "请上传图片二进制内容，使用 application/octet-stream")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "图片不得超过 5 MiB")
        data.extend(chunk)
    image = await run_in_threadpool(sanitize_image, filename, bytes(data))

    def save():
        begin_write(db)
        revalidate_write_access(db, access.workspace.id, auth)
        result = service.public(
            service.create(db, access.workspace.id, auth.user.id, filename, image)
        )
        db.commit()
        return result

    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(save)


@router.get("/sample")
def sample(access: WorkspaceAccess = Depends(get_workspace_access)):
    return Response(
        sample_receipt(),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="FinScope-demo-receipt.jpg"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("", response_model=list[ReceiptSummary])
def history(
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    items = db.scalars(
        select(Attachment)
        .where(Attachment.workspace_id == access.workspace.id)
        .order_by(Attachment.created_at.desc())
        .limit(50)
    ).all()
    result = [service.summary(item) for item in items]
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{receipt_id}", response_model=ReceiptPublic)
def read(
    receipt_id: str,
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    result = service.public(service.get_receipt(db, access.workspace.id, receipt_id))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{receipt_id}/image")
def image(
    receipt_id: str,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    item = service.get_receipt(db, access.workspace.id, receipt_id)
    data = item.image_data
    db.commit()
    if data is None:
        raise HTTPException(410, "票据图片已过期或清理")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="receipt.jpg"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/{receipt_id}/recognize", response_model=ReceiptPublic)
def recognize(
    receipt_id: str,
    payload: VersionRequest,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    provider: OcrProvider = Depends(get_ocr_provider),
):
    maintenance(db, access.workspace.id)
    item = service.get_receipt(db, access.workspace.id, receipt_id)
    service.ensure_draft(item, payload.version)
    image_data = item.image_data
    workspace_id, user_id = access.workspace.id, auth.user.id
    db.commit()  # OCR 耗时期间释放数据库写锁，保存时重新检查版本。
    notices, text, provider_name = [], "", ""
    with service.processing_slot(user_id):
        try:
            result = provider.recognize(image_data)
            text, provider_name = result.text[:12000], result.provider
        except OcrUnavailable as exc:
            notices = [str(exc)]
    candidate = RuleReceiptExtractor().extract(text)
    begin_write(db)
    lock_workspace(db, workspace_id)
    revalidate_write_access(db, workspace_id, auth)
    item = service.get_receipt(db, workspace_id, receipt_id, lock=True)
    service.ensure_draft(item, payload.version)
    item.ocr_text, item.ocr_provider = text, provider_name
    item.ocr_status = "ready" if text.strip() else "failed"
    item.reviewed_payload = None
    item.candidates, item.extraction_provider = (
        candidate.model_dump(mode="json"),
        "rules",
    )
    item.notices = notices + ["本地 OCR 未提供可用的逐字段准确率；所有信息须人工核对。"]
    item.version += 1
    db.flush()
    audit(
        db,
        workspace_id,
        user_id,
        "recognize",
        "receipt",
        item.id,
        None,
        {"ocr_status": item.ocr_status, "provider": provider_name},
    )
    result = service.public(item)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{receipt_id}/structure", response_model=ReceiptPublic)
def structure(
    receipt_id: str,
    payload: StructureRequest,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    extractor=Depends(get_ai_extractor),
):
    maintenance(db, access.workspace.id)
    item = service.get_receipt(db, access.workspace.id, receipt_id)
    service.ensure_draft(item, payload.version)
    text = payload.text if payload.text is not None else item.ocr_text or ""
    if not text.strip():
        raise HTTPException(422, "请先识别图片或输入修订文字；也可直接手工填写下方交易")
    if payload.use_ai:
        if not payload.consent:
            raise HTTPException(403, "发送识别文字前须明确同意外部处理")
        config = ai_configuration()
        if not config["enabled"]:
            raise HTTPException(422, "AI 未配置，可使用本地整理或手工填写")
        if payload.consent_target != f"{config['base']} / {config['model']}":
            raise HTTPException(409, "AI 接收方配置已变更，请刷新并重新核对授权")
        if text != (item.ocr_text or ""):
            raise HTTPException(422, "请先保存文字并本地整理，再核对发送预览")
    workspace_id, user_id = access.workspace.id, auth.user.id
    db.commit()
    candidate, extraction_provider, notices = (
        RuleReceiptExtractor().extract(text),
        "rules",
        [],
    )
    if payload.use_ai:
        with service.processing_slot(user_id):
            try:
                candidate = extractor.extract(redact_text(text))
                extraction_provider = "compatible_ai"
            except ExtractionUnavailable as exc:
                notices = [str(exc)]
    begin_write(db)
    lock_workspace(db, workspace_id)
    revalidate_write_access(db, workspace_id, auth)
    item = service.get_receipt(db, workspace_id, receipt_id, lock=True)
    service.ensure_draft(item, payload.version)
    item.ocr_text = text
    item.reviewed_payload = None
    item.candidates, item.extraction_provider, item.notices = (
        candidate.model_dump(mode="json"),
        extraction_provider,
        notices,
    )
    item.version += 1
    db.flush()
    audit(
        db,
        workspace_id,
        user_id,
        "structure",
        "receipt",
        item.id,
        None,
        {
            "provider": extraction_provider,
            "external_consent": payload.use_ai and payload.consent,
            "consent_target": payload.consent_target if payload.use_ai else None,
            "text_hash": hashlib.sha256(redact_text(text).encode()).hexdigest()
            if payload.use_ai
            else None,
        },
    )
    result = service.public(item)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/{receipt_id}/review", response_model=ReceiptPublic)
def review(
    receipt_id: str,
    payload: ReceiptReview,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    item = service.get_receipt(db, access.workspace.id, receipt_id, lock=True)
    service.ensure_draft(item, payload.version)
    service.validate_review(access.workspace, item, payload)
    validate_transaction(db, access.workspace.id, payload.transaction)
    item.reviewed_payload = payload.model_dump(mode="json", exclude={"version"})
    item.version += 1
    db.flush()
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "review",
        "receipt",
        item.id,
        None,
        {"human_reviewed": True},
    )
    result = service.public(item)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{receipt_id}/confirm", response_model=ReceiptSummary)
def confirm(
    receipt_id: str,
    payload: ReceiptConfirm,
    idempotency_key: IdempotencyKey,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    result = idempotent_create(
        db,
        access.workspace.id,
        auth.user.id,
        idempotency_key,
        "receipt_confirm",
        payload,
        lambda: service.confirm(
            db, access.workspace, receipt_id, auth.user.id, payload
        ),
        serializer=lambda item: service.summary(item).model_dump(mode="json"),
        context=receipt_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{receipt_id}/clear", response_model=ReceiptPublic)
def clear(
    receipt_id: str,
    payload: VersionRequest,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    item = service.get_receipt(db, access.workspace.id, receipt_id, lock=True)
    if item.version != payload.version:
        raise HTTPException(409, "票据已变更，请重新打开")
    item.image_data, item.ocr_text, item.candidates = None, None, None
    item.reviewed_payload = None
    item.expires_at = datetime.now(UTC)
    if item.status == "draft":
        item.status = "cancelled"
    item.version += 1
    db.flush()
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "clear",
        "receipt",
        item.id,
        None,
        {"transaction_preserved": item.transaction_id is not None},
    )
    result = service.public(item)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
