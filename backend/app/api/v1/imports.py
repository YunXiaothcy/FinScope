from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.v1.finance import write_access
from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.session import get_db
from app.models import ImportBatch
from app.schemas.finance import VersionRequest
from app.schemas.import_batch import (
    ConfirmRequest,
    ImportPublic,
    ImportSummary,
    PreviewRequest,
)
from app.security.access import revalidate_write_access
from app.security.auth import AuthContext, require_csrf
from app.services import import_batch as service
from app.services.finance import audit, begin_write, idempotent_create
from app.services.import_parser import MAX_BYTES, parse_file, validate_filename

router = APIRouter(prefix="/workspaces/{workspace_id}/imports", tags=["imports"])
IdempotencyKey = Annotated[
    str, Header(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
]


def maintenance(db: Session, workspace_id: str):
    begin_write(db)
    service.lock_workspace(db, workspace_id)
    service.purge_expired(db, workspace_id)


@router.post("", response_model=ImportPublic, status_code=201)
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
    validate_filename(filename)
    if (
        request.headers.get("content-type", "").split(";")[0]
        != "application/octet-stream"
    ):
        raise HTTPException(
            415, "请直接上传文件内容，Content-Type 使用 application/octet-stream"
        )
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "文件不得超过 2 MiB")
    parsed = await run_in_threadpool(parse_file, filename, bytes(data))

    def save():
        begin_write(db)
        revalidate_write_access(db, access.workspace.id, auth)
        batch = service.create_batch(
            db, access.workspace.id, auth.user.id, filename, bytes(data), parsed
        )
        result = service.public_batch(db, batch)
        db.commit()
        return result

    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(save)


@router.get("", response_model=list[ImportSummary])
def history(
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    batches = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.workspace_id == access.workspace.id)
        .order_by(ImportBatch.created_at.desc())
        .limit(50)
    ).all()
    result = [service.summary(db, batch) for batch in batches]
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{batch_id}", response_model=ImportPublic)
def read_batch(
    batch_id: str,
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    result = service.public_batch(
        db, service.get_batch(db, access.workspace.id, batch_id)
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/{batch_id}/preview", response_model=ImportPublic)
def preview(
    batch_id: str,
    payload: PreviewRequest,
    response: Response,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    batch = service.get_batch(db, access.workspace.id, batch_id, lock=True)
    service.preview(db, access.workspace, batch, payload)
    result = service.public_batch(db, batch)
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "preview",
        "import_batch",
        batch.id,
        None,
        service.summary(db, batch).model_dump(mode="json"),
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{batch_id}/confirm", response_model=ImportSummary)
def confirm(
    batch_id: str,
    payload: ConfirmRequest,
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
        "import_confirm",
        payload,
        lambda: service.confirm(db, access.workspace, batch_id, auth.user.id, payload),
        serializer=lambda batch: service.summary(db, batch).model_dump(mode="json"),
        context=batch_id,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{batch_id}/cancel", response_model=ImportSummary)
def cancel(
    batch_id: str,
    payload: VersionRequest,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    maintenance(db, access.workspace.id)
    batch = service.get_batch(db, access.workspace.id, batch_id, lock=True)
    service.ensure_draft(batch, payload.version)
    batch.status = "cancelled"
    batch.mapping = None
    for row in service.batch_rows(db, batch):
        row.raw, row.normalized, row.parsing_errors = None, None, []
        row.status, row.messages = "skipped", ["批次已取消"]
    db.flush()
    result = service.summary(db, batch)
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "cancel",
        "import_batch",
        batch.id,
        None,
        result.model_dump(mode="json"),
    )
    db.commit()
    return result
