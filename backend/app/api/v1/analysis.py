from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.receipts import IdempotencyKey
from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.session import get_db
from app.models import AnalysisLog
from app.schemas.analysis import AnalysisPublic, AskRequest, ExplainRequest
from app.security.auth import AuthContext, get_auth, require_csrf
from app.services import analysis as service
from app.services.analysis_tools import build_snapshot
from app.services.finance import audit, begin_write
from app.services.import_batch import lock_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}/analysis", tags=["analysis"])


def prepare(db: Session, workspace_id: str, user_id: str):
    begin_write(db)
    lock_workspace(db, workspace_id)
    service.maintenance(db, workspace_id, user_id)
    # 正式 Session 关闭 autoflush；重新读取前先落下过期/中断状态。
    db.flush()


@router.get("/capabilities")
def capabilities(
    response: Response, access: WorkspaceAccess = Depends(get_workspace_access)
):
    response.headers["Cache-Control"] = "no-store"
    return {
        "ai_enabled": False,
        "integration_status": "reserved",
        "target": "",
        "retention_days": 30,
        "disclosure": "AI 接口已预留，当前不接入任何外部 API、不读取财务分析 API 密钥。可直接使用本地统计与分析。",
    }


@router.post("", response_model=AnalysisPublic, status_code=201)
def ask(
    payload: AskRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    access: WorkspaceAccess = Depends(get_workspace_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    prepare(db, access.workspace.id, auth.user.id)
    fingerprint = service.digest(payload.model_dump(mode="json"))
    old = db.scalar(
        select(AnalysisLog).where(
            AnalysisLog.workspace_id == access.workspace.id,
            AnalysisLog.created_by == auth.user.id,
            AnalysisLog.request_key == idempotency_key,
        )
    )
    if old is not None:
        if old.request_hash != fingerprint:
            raise HTTPException(409, "同一请求编号不能用于不同问题")
        result = service.public(old)
    else:
        with service.processing_slot(auth.user.id):
            snapshot = build_snapshot(db, access.workspace, payload)
        row = AnalysisLog(
            workspace_id=access.workspace.id,
            created_by=auth.user.id,
            request_key=idempotency_key,
            request_hash=fingerprint,
            snapshot_hash=service.digest(snapshot["cloud_payload"]),
            snapshot=snapshot,
            selection=service.local_selection(snapshot),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(row)
        db.flush()
        audit(
            db,
            row.workspace_id,
            auth.user.id,
            "analyze",
            "ai_analysis",
            row.id,
            None,
            {
                "snapshot_hash": row.snapshot_hash,
                "intent": snapshot["intent"],
                "tools": [tool["name"] for tool in snapshot["tools"]],
            },
        )
        result = service.public(row)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("", response_model=list[AnalysisPublic])
def history(
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    prepare(db, access.workspace.id, auth.user.id)
    rows = db.scalars(
        select(AnalysisLog)
        .where(
            AnalysisLog.workspace_id == access.workspace.id,
            AnalysisLog.created_by == auth.user.id,
            AnalysisLog.status != "expired",
        )
        .order_by(AnalysisLog.created_at.desc())
        .limit(20)
    ).all()
    result = [service.public(row) for row in rows]
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{record_id}", response_model=AnalysisPublic)
def detail(
    record_id: str,
    response: Response,
    access: WorkspaceAccess = Depends(get_workspace_access),
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
):
    prepare(db, access.workspace.id, auth.user.id)
    result = service.public(
        service.get_record(db, access.workspace.id, auth.user.id, record_id)
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{record_id}/explain", response_model=AnalysisPublic)
def explain(
    record_id: str,
    payload: ExplainRequest,
    access: WorkspaceAccess = Depends(get_workspace_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    # 用户要求仅预留接口：不读取配置、不构造 Provider、不发送外部请求。
    service.get_record(db, access.workspace.id, auth.user.id, record_id)
    raise HTTPException(501, "AI 分析接口已预留，暂未接入外部服务；请使用本地分析")


@router.delete("/{record_id}", status_code=204)
def clear(
    record_id: str,
    access: WorkspaceAccess = Depends(get_workspace_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    prepare(db, access.workspace.id, auth.user.id)
    row = service.get_record(db, access.workspace.id, auth.user.id, record_id)
    if row.status == "running":
        raise HTTPException(409, "云端处理中，请稍后刷新再清理")
    row.status, row.snapshot, row.selection, row.target = "expired", {}, {}, ""
    audit(
        db,
        row.workspace_id,
        auth.user.id,
        "clear_analysis",
        "ai_analysis",
        row.id,
        None,
        {"snapshot_hash": row.snapshot_hash},
    )
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
