from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.session import get_db
from app.models import ReceivablePayable
from app.schemas.business import (
    BusinessReport,
    ObligationCreate,
    ObligationPublic,
    SettlementCreate,
)
from app.schemas.finance import TransactionPublic, VersionRequest
from app.security.auth import AuthContext, require_csrf
from app.services.business import (
    business_report,
    create_item,
    get_item,
    public_item,
    settle_item,
    settlement_transactions,
)
from app.services.finance import audit, begin_write, idempotent_create

router = APIRouter(
    prefix="/workspaces/{workspace_id}/business", tags=["business finance"]
)
IdempotencyKey = Annotated[
    str, Header(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
]


def business_access(
    access: WorkspaceAccess = Depends(get_workspace_access),
) -> WorkspaceAccess:
    if access.workspace.type != "business":
        raise HTTPException(403, "此功能仅用于商户空间")
    return access


def business_write(
    access: WorkspaceAccess = Depends(business_access),
    _auth: AuthContext = Depends(require_csrf),
) -> WorkspaceAccess:
    if access.member.role == "viewer":
        raise HTTPException(403, "只读成员不能修改商户账务")
    return access


@router.get("/report", response_model=BusinessReport)
def report(
    start_date: date | None = None,
    end_date: date | None = None,
    access: WorkspaceAccess = Depends(business_access),
    db: Session = Depends(get_db),
):
    return business_report(
        db, access.workspace.id, access.workspace.base_currency, start_date, end_date
    )


@router.post("/obligations", response_model=ObligationPublic, status_code=201)
def create_obligation(
    payload: ObligationCreate,
    idempotency_key: IdempotencyKey,
    access: WorkspaceAccess = Depends(business_write),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    return idempotent_create(
        db,
        access.workspace.id,
        auth.user.id,
        idempotency_key,
        "business_obligation",
        payload,
        lambda: create_item(
            db,
            access.workspace.id,
            access.workspace.base_currency,
            auth.user.id,
            payload,
        ),
        serializer=lambda item: public_item(db, item).model_dump(mode="json"),
    )


@router.get("/obligations", response_model=list[ObligationPublic])
def list_obligations(
    kind: Literal["receivable", "payable"] | None = None,
    state: Literal["unsettled", "partial", "settled", "voided"] | None = None,
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    access: WorkspaceAccess = Depends(business_access),
    db: Session = Depends(get_db),
):
    statement = (
        select(ReceivablePayable)
        .where(ReceivablePayable.workspace_id == access.workspace.id)
        .order_by(ReceivablePayable.recognized_at.desc(), ReceivablePayable.id)
    )
    if kind:
        statement = statement.where(ReceivablePayable.kind == kind)
    items = [public_item(db, item) for item in db.scalars(statement).all()]
    if state:
        items = [item for item in items if item.settlement_status == state]
    return items[offset : offset + limit]


@router.get("/obligations/{item_id}", response_model=ObligationPublic)
def read_obligation(
    item_id: str,
    access: WorkspaceAccess = Depends(business_access),
    db: Session = Depends(get_db),
):
    return public_item(db, get_item(db, access.workspace.id, item_id))


@router.get(
    "/obligations/{item_id}/settlements", response_model=list[TransactionPublic]
)
def list_settlements(
    item_id: str,
    access: WorkspaceAccess = Depends(business_access),
    db: Session = Depends(get_db),
):
    get_item(db, access.workspace.id, item_id)
    return settlement_transactions(db, access.workspace.id, item_id)


@router.post(
    "/obligations/{item_id}/settlements",
    response_model=TransactionPublic,
    status_code=201,
)
def settle_obligation(
    item_id: str,
    payload: SettlementCreate,
    idempotency_key: IdempotencyKey,
    access: WorkspaceAccess = Depends(business_write),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    return idempotent_create(
        db,
        access.workspace.id,
        auth.user.id,
        idempotency_key,
        "business_settlement",
        payload,
        lambda: settle_item(db, access.workspace.id, item_id, auth.user.id, payload),
        context=item_id,
    )


@router.post("/obligations/{item_id}/void", response_model=ObligationPublic)
def void_obligation(
    item_id: str,
    payload: VersionRequest,
    access: WorkspaceAccess = Depends(business_write),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    item = get_item(db, access.workspace.id, item_id, lock=True)
    if item.status != "posted" or item.version != payload.version:
        raise HTTPException(409, "应收应付已变更，请刷新")
    before = public_item(db, item)
    if before.paid_amount:
        raise HTTPException(422, "请先撤销有效结算，再撤销应收应付")
    item.status = "voided"
    db.flush()
    after = public_item(db, item)
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "void",
        "obligation",
        item.id,
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
    )
    db.commit()
    return after
