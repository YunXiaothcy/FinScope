from datetime import UTC, date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.money import ZERO, validate_money
from app.db.session import get_db
from app.models import Account, AuditLog, Category, FundsMovement, Transaction, Transfer
from app.schemas.finance import (
    AccountCreate,
    AccountPublic,
    AccountUpdate,
    AuditPublic,
    CategoryCreate,
    CategoryPublic,
    MovementPublic,
    SummaryPublic,
    TransactionCreate,
    TransactionEdit,
    TransactionPublic,
    TransactionsPage,
    TransferCreate,
    TransferPublic,
    VersionRequest,
)
from app.security.auth import AuthContext, require_csrf
from app.services.business import prepare_settlement_void, public_item
from app.services.finance import (
    audit,
    balances,
    begin_write,
    check_currency,
    get_account,
    get_transaction,
    has_refunds,
    idempotent_create,
    period_bounds,
    post_transaction,
    reverse_movements,
    snapshot,
    validate_transaction,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/finance", tags=["personal finance"]
)


def write_access(
    access: WorkspaceAccess = Depends(get_workspace_access),
    _auth: AuthContext = Depends(require_csrf),
) -> WorkspaceAccess:
    if access.member.role == "viewer":
        raise HTTPException(403, "只读成员不能修改账务")
    return access


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "名称重复或记录发生冲突") from exc


def category_kind(
    payload: CategoryCreate, access: WorkspaceAccess, existing: Category | None = None
) -> str:
    kind = payload.business_kind or (
        existing.business_kind
        if existing
        else "revenue"
        if payload.type == "income"
        else "expense"
    )
    if (payload.type == "income" and kind != "revenue") or (
        payload.type == "expense" and kind not in {"cost", "expense"}
    ):
        raise HTTPException(422, "分类类型与经营口径不一致")
    if access.workspace.type != "business" and kind == "cost":
        raise HTTPException(422, "经营成本分类仅用于商户空间")
    if existing and kind != existing.business_kind:
        raise HTTPException(422, "经营口径创建后不能改写，请创建新分类并更正相关交易")
    return kind


@router.get("/accounts", response_model=list[AccountPublic])
def list_accounts(
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return balances(db, access.workspace.id)


@router.post("/accounts", response_model=AccountPublic, status_code=201)
def create_account(
    payload: AccountCreate,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    check_currency(access.workspace.base_currency)
    begin_write(db)
    account = Account(
        workspace_id=access.workspace.id,
        currency=access.workspace.base_currency,
        **payload.model_dump(),
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "账户名称已存在") from exc
    after = {
        "name": account.name,
        "type": account.type,
        "opening_balance": str(account.opening_balance),
        "currency": account.currency,
        "is_active": True,
    }
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "create",
        "account",
        account.id,
        None,
        after,
    )
    commit(db)
    return next(a for a in balances(db, access.workspace.id) if a.id == account.id)


@router.patch("/accounts/{account_id}", response_model=AccountPublic)
def update_account(
    account_id: str,
    payload: AccountUpdate,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    account = get_account(db, access.workspace.id, account_id, active=False)
    before = {"name": account.name, "is_active": account.is_active}
    account.name, account.is_active = payload.name, payload.is_active
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "update",
        "account",
        account.id,
        before,
        payload.model_dump(),
    )
    commit(db)
    return next(a for a in balances(db, access.workspace.id) if a.id == account.id)


@router.get("/accounts/{account_id}/movements", response_model=list[MovementPublic])
def list_movements(
    account_id: str,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    get_account(db, access.workspace.id, account_id, active=False)
    return db.scalars(
        select(FundsMovement)
        .where(
            FundsMovement.workspace_id == access.workspace.id,
            FundsMovement.account_id == account_id,
        )
        .order_by(FundsMovement.created_at.desc())
        .limit(200)
    ).all()


@router.get("/categories", response_model=list[CategoryPublic])
def list_categories(
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Category)
        .where(Category.workspace_id == access.workspace.id)
        .order_by(Category.type, Category.name)
    ).all()


@router.post("/categories", response_model=CategoryPublic, status_code=201)
def create_category(
    payload: CategoryCreate,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    category = Category(
        workspace_id=access.workspace.id,
        **payload.model_dump(exclude={"business_kind"}),
        business_kind=category_kind(payload, access),
    )
    db.add(category)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "此类型下已有同名分类") from exc
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "create",
        "category",
        category.id,
        None,
        payload.model_dump(),
    )
    commit(db)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryPublic)
def rename_category(
    category_id: str,
    payload: CategoryCreate,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    category = db.scalar(
        select(Category).where(
            Category.id == category_id, Category.workspace_id == access.workspace.id
        )
    )
    if category is None:
        raise HTTPException(404, "分类不存在于当前空间")
    if category.type != payload.type:
        raise HTTPException(422, "分类类型创建后不能更改")
    category_kind(payload, access, category)
    before = {
        "name": category.name,
        "type": category.type,
        "business_kind": category.business_kind,
    }
    category.name = payload.name
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "update",
        "category",
        category.id,
        before,
        payload.model_dump(),
    )
    commit(db)
    return category


@router.post("/transactions", response_model=TransactionPublic, status_code=201)
def create_transaction(
    payload: TransactionCreate,
    idempotency_key: str = Header(
        min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    ),
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    return idempotent_create(
        db,
        access.workspace.id,
        auth.user.id,
        idempotency_key,
        "transaction",
        payload,
        lambda: post_transaction(db, access.workspace.id, auth.user.id, payload),
    )


@router.get("/transactions", response_model=TransactionsPage)
def list_transactions(
    start_date: date | None = None,
    end_date: date | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    type: str | None = Query(
        default=None, pattern="^(income|expense|refund|adjustment)$"
    ),
    min_amount: str | None = None,
    max_amount: str | None = None,
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    conditions = [Transaction.workspace_id == access.workspace.id]
    if start_date is not None or end_date is not None:
        _, _, start, end = period_bounds(start_date, end_date)
        conditions.extend(
            [Transaction.occurred_at >= start, Transaction.occurred_at < end]
        )
    if account_id:
        get_account(db, access.workspace.id, account_id, active=False)
        conditions.append(Transaction.account_id == account_id)
    if category_id:
        if (
            db.scalar(
                select(Category.id).where(
                    Category.id == category_id,
                    Category.workspace_id == access.workspace.id,
                )
            )
            is None
        ):
            raise HTTPException(404, "分类不存在于当前空间")
        conditions.append(Transaction.category_id == category_id)
    if type:
        conditions.append(Transaction.type == type)
    try:
        minimum = validate_money(min_amount) if min_amount is not None else None
        maximum = validate_money(max_amount) if max_amount is not None else None
    except ValueError as exc:
        raise HTTPException(422, "金额筛选格式无效") from exc
    if (
        (minimum is not None and minimum < 0)
        or (maximum is not None and maximum < 0)
        or (minimum is not None and maximum is not None and minimum > maximum)
    ):
        raise HTTPException(422, "金额筛选范围无效")
    if minimum is not None:
        conditions.append(Transaction.amount >= minimum)
    if maximum is not None:
        conditions.append(Transaction.amount <= maximum)
    if q:
        conditions.append(
            Transaction.description.contains(q, autoescape=True)
            | Transaction.merchant_name.contains(q, autoescape=True)
        )
    total = db.scalar(select(func.count()).select_from(Transaction).where(*conditions))
    records = db.scalars(
        select(Transaction)
        .where(*conditions)
        .order_by(Transaction.occurred_at.desc(), Transaction.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return TransactionsPage(
        items=[TransactionPublic.model_validate(item) for item in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.put("/transactions/{transaction_id}", response_model=TransactionPublic)
def edit_transaction(
    transaction_id: str,
    payload: TransactionEdit,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    record = get_transaction(db, access.workspace.id, transaction_id, lock=True)
    if record.version != payload.version or record.status != "posted":
        raise HTTPException(409, "交易已被修改或撤销，请刷新")
    if record.source == "settlement":
        raise HTTPException(422, "应收应付结算不能直接编辑，请撤销后重新结算")
    if record.type == "refund" or has_refunds(db, record):
        raise HTTPException(422, "退款请撤销后重新登记；原交易有退款时不能直接编辑")
    if record.type != payload.type:
        raise HTTPException(422, "编辑不能改变交易类型，请撤销后重新登记")
    account, category_id, effect = validate_transaction(
        db, access.workspace.id, payload
    )
    before = snapshot(record)
    reverse_movements(db, record)
    for name, value in payload.model_dump(exclude={"version", "category_id"}).items():
        setattr(record, name, value)
    record.category_id = category_id
    record.currency = account.currency
    record.occurred_at = payload.occurred_at.astimezone(UTC)
    db.add(
        FundsMovement(
            workspace_id=access.workspace.id,
            account_id=account.id,
            transaction_id=record.id,
            amount=effect,
        )
    )
    # 即使只更新备注也推进版本；禁止客户端覆盖当前版本。
    record.version += 1
    db.flush()
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "edit",
        "transaction",
        record.id,
        before,
        snapshot(record),
    )
    commit(db)
    return record


@router.post("/transactions/{transaction_id}/void", response_model=TransactionPublic)
def void_transaction(
    transaction_id: str,
    payload: VersionRequest,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    record = get_transaction(db, access.workspace.id, transaction_id, lock=True)
    if record.version != payload.version or record.status != "posted":
        raise HTTPException(409, "交易已被修改或撤销，请刷新")
    if has_refunds(db, record):
        raise HTTPException(422, "请先撤销关联退款，再撤销原交易")
    parent_change = prepare_settlement_void(db, access.workspace.id, record)
    before = snapshot(record)
    reverse_movements(db, record)
    record.status = "voided"
    db.flush()
    if parent_change:
        item, parent_before = parent_change
        audit(
            db,
            access.workspace.id,
            auth.user.id,
            "reopen",
            "obligation",
            item.id,
            parent_before,
            public_item(db, item).model_dump(mode="json"),
        )
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "void",
        "transaction",
        record.id,
        before,
        snapshot(record),
    )
    commit(db)
    return record


@router.post("/transfers", response_model=TransferPublic, status_code=201)
def create_transfer(
    payload: TransferCreate,
    idempotency_key: str = Header(
        min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"
    ),
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    def build():
        source = get_account(db, access.workspace.id, payload.from_account_id)
        target = get_account(db, access.workspace.id, payload.to_account_id)
        if source.id == target.id or source.currency != target.currency:
            raise HTTPException(422, "转账需要两个不同的同币种账户")
        transfer = Transfer(
            workspace_id=access.workspace.id,
            created_by=auth.user.id,
            currency=source.currency,
            **payload.model_dump(),
        )
        transfer.occurred_at = payload.occurred_at.astimezone(UTC)
        db.add(transfer)
        db.flush()
        db.add_all(
            [
                FundsMovement(
                    workspace_id=access.workspace.id,
                    account_id=source.id,
                    transfer_id=transfer.id,
                    amount=-payload.amount,
                ),
                FundsMovement(
                    workspace_id=access.workspace.id,
                    account_id=target.id,
                    transfer_id=transfer.id,
                    amount=payload.amount,
                ),
            ]
        )
        return transfer

    return idempotent_create(
        db,
        access.workspace.id,
        auth.user.id,
        idempotency_key,
        "transfer",
        payload,
        build,
    )


@router.get("/transfers", response_model=list[TransferPublic])
def list_transfers(
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Transfer)
        .where(Transfer.workspace_id == access.workspace.id)
        .order_by(Transfer.occurred_at.desc())
        .limit(200)
    ).all()


@router.post("/transfers/{transfer_id}/void", response_model=TransferPublic)
def void_transfer(
    transfer_id: str,
    payload: VersionRequest,
    access: WorkspaceAccess = Depends(write_access),
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    begin_write(db)
    record = db.scalar(
        select(Transfer)
        .where(Transfer.id == transfer_id, Transfer.workspace_id == access.workspace.id)
        .with_for_update()
    )
    if record is None:
        raise HTTPException(404, "转账不存在于当前空间")
    if record.version != payload.version or record.status != "posted":
        raise HTTPException(409, "转账已被修改或撤销，请刷新")
    before = snapshot(record)
    reverse_movements(db, record)
    record.status = "voided"
    db.flush()
    audit(
        db,
        access.workspace.id,
        auth.user.id,
        "void",
        "transfer",
        record.id,
        before,
        snapshot(record),
    )
    commit(db)
    return record


@router.get("/summary", response_model=SummaryPublic)
def summary(
    start_date: date | None = None,
    end_date: date | None = None,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    first, last, start, end = period_bounds(start_date, end_date)
    records = db.scalars(
        select(Transaction).where(
            Transaction.workspace_id == access.workspace.id,
            Transaction.status == "posted",
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
    ).all()
    income, expense, adjustments = ZERO, ZERO, ZERO
    category_totals: dict[str, Decimal] = {}
    for item in records:
        kind, amount = item.type, item.amount
        if kind == "refund":
            kind = get_transaction(
                db, access.workspace.id, item.related_transaction_id
            ).type
            amount = -amount
        if kind == "income":
            income += amount
        elif kind == "expense":
            expense += amount
        elif kind == "adjustment":
            adjustments += (
                amount if item.adjustment_direction == "increase" else -amount
            )
        if item.category_id:
            category_totals[item.category_id] = (
                category_totals.get(item.category_id, ZERO) + amount
            )
    categories = db.scalars(
        select(Category).where(Category.workspace_id == access.workspace.id)
    ).all()
    return SummaryPublic(
        currency=access.workspace.base_currency,
        start_date=first,
        end_date=last,
        income=income,
        expense=expense,
        net=income - expense,
        adjustment_net=adjustments,
        cash_flow=income - expense + adjustments,
        total_balance=sum((a.balance for a in balances(db, access.workspace.id)), ZERO),
        categories=[
            {
                "category_id": c.id,
                "name": c.name,
                "type": c.type,
                "amount": category_totals[c.id],
            }
            for c in categories
            if c.id in category_totals
        ],
    )


@router.get("/audit-logs", response_model=list[AuditPublic])
def list_audit(
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(AuditLog)
        .where(AuditLog.workspace_id == access.workspace.id)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    ).all()
