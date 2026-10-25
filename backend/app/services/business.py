from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.money import ZERO
from app.models import BusinessSettlement, Category, ReceivablePayable, Transaction
from app.schemas.business import (
    BusinessDay,
    BusinessReport,
    ObligationCreate,
    ObligationPublic,
    SettlementCreate,
)
from app.schemas.finance import TransactionCreate
from app.services.finance import (
    HK,
    audit,
    balances,
    check_currency,
    get_account,
    period_bounds,
    post_transaction,
)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def get_item(
    db: Session, workspace_id: str, item_id: str, lock: bool = False
) -> ReceivablePayable:
    statement = select(ReceivablePayable).where(
        ReceivablePayable.id == item_id, ReceivablePayable.workspace_id == workspace_id
    )
    item = db.scalar(statement.with_for_update() if lock else statement)
    if item is None:
        raise HTTPException(404, "应收应付不存在于当前空间")
    return item


def settlement_transactions(
    db: Session, workspace_id: str, item_id: str
) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .join(
                BusinessSettlement, BusinessSettlement.transaction_id == Transaction.id
            )
            .where(
                BusinessSettlement.workspace_id == workspace_id,
                BusinessSettlement.item_id == item_id,
                Transaction.workspace_id == workspace_id,
            )
            .order_by(Transaction.occurred_at)
        ).all()
    )


def public_item(db: Session, item: ReceivablePayable) -> ObligationPublic:
    paid = sum(
        (
            t.amount
            for t in settlement_transactions(db, item.workspace_id, item.id)
            if t.status == "posted"
        ),
        ZERO,
    )
    remaining = item.amount - paid if item.status == "posted" else ZERO
    state = (
        "voided"
        if item.status == "voided"
        else "settled"
        if remaining == ZERO
        else "partial"
        if paid
        else "unsettled"
    )
    data = {
        name: getattr(item, name)
        for name in (
            "id",
            "kind",
            "category_id",
            "amount",
            "currency",
            "recognized_at",
            "due_date",
            "counterparty",
            "description",
            "status",
            "version",
        )
    }
    return ObligationPublic(
        **data,
        paid_amount=paid,
        remaining_amount=remaining,
        settlement_status=state,
        is_overdue=remaining > ZERO and item.due_date < datetime.now(HK).date(),
    )


def create_item(
    db: Session,
    workspace_id: str,
    currency: str,
    user_id: str,
    payload: ObligationCreate,
) -> ReceivablePayable:
    check_currency(currency)
    category = db.scalar(
        select(Category).where(
            Category.id == payload.category_id, Category.workspace_id == workspace_id
        )
    )
    if category is None:
        raise HTTPException(404, "分类不存在于当前空间")
    if (payload.kind == "receivable") != (category.type == "income"):
        raise HTTPException(422, "应收必须使用收入分类，应付必须使用成本或费用分类")
    if payload.due_date < payload.recognized_at.astimezone(HK).date():
        raise HTTPException(422, "到期日不能早于业务确认日期")
    item = ReceivablePayable(
        workspace_id=workspace_id,
        currency=currency,
        created_by=user_id,
        **payload.model_dump(),
    )
    item.recognized_at = payload.recognized_at.astimezone(UTC)
    db.add(item)
    db.flush()
    return item


def settle_item(
    db: Session,
    workspace_id: str,
    item_id: str,
    user_id: str,
    payload: SettlementCreate,
) -> Transaction:
    item = get_item(db, workspace_id, item_id, lock=True)
    if item.status != "posted" or item.version != payload.version:
        raise HTTPException(409, "应收应付已变更，请刷新后重试")
    before = public_item(db, item)
    if payload.amount > before.remaining_amount:
        raise HTTPException(422, "本次结算金额不能超过未结余额")
    if payload.occurred_at < aware(item.recognized_at):
        raise HTTPException(422, "本阶段结算时间不能早于业务确认时间")
    if get_account(db, workspace_id, payload.account_id).currency != item.currency:
        raise HTTPException(422, "结算账户币种与应收应付不一致")
    transaction = post_transaction(
        db,
        workspace_id,
        user_id,
        TransactionCreate(
            account_id=payload.account_id,
            category_id=item.category_id,
            type="income" if item.kind == "receivable" else "expense",
            amount=payload.amount,
            occurred_at=payload.occurred_at,
            description=f"应收应付结算：{item.description}"[:500],
            merchant_name=item.counterparty,
        ),
    )
    transaction.source = "settlement"
    db.add(
        BusinessSettlement(
            workspace_id=workspace_id, item_id=item.id, transaction_id=transaction.id
        )
    )
    item.version += 1
    db.flush()
    audit(
        db,
        workspace_id,
        user_id,
        "settle",
        "obligation",
        item.id,
        before.model_dump(mode="json"),
        public_item(db, item).model_dump(mode="json"),
    )
    return transaction


def prepare_settlement_void(db: Session, workspace_id: str, transaction: Transaction):
    if transaction.source != "settlement":
        return None
    link = db.scalar(
        select(BusinessSettlement).where(
            BusinessSettlement.transaction_id == transaction.id,
            BusinessSettlement.workspace_id == workspace_id,
        )
    )
    if link is None:
        raise HTTPException(409, "结算关联记录缺失，无法撤销")
    item = get_item(db, workspace_id, link.item_id, lock=True)
    before = public_item(db, item).model_dump(mode="json")
    item.version += 1
    return item, before


def business_report(
    db: Session,
    workspace_id: str,
    currency: str,
    start_date: date | None,
    end_date: date | None,
) -> BusinessReport:
    first, last, start, end = period_bounds(start_date, end_date)
    categories = {
        c.id: c
        for c in db.scalars(
            select(Category).where(Category.workspace_id == workspace_id)
        ).all()
    }
    transactions = list(
        db.scalars(
            select(Transaction).where(
                Transaction.workspace_id == workspace_id, Transaction.status == "posted"
            )
        ).all()
    )
    transaction_by_id = {t.id: t for t in transactions}
    links = list(
        db.scalars(
            select(BusinessSettlement).where(
                BusinessSettlement.workspace_id == workspace_id
            )
        ).all()
    )
    linked_ids = {link.transaction_id for link in links}
    obligations = list(
        db.scalars(
            select(ReceivablePayable).where(
                ReceivablePayable.workspace_id == workspace_id,
                ReceivablePayable.status == "posted",
            )
        ).all()
    )
    category_amounts: dict[str, Decimal] = {}
    days: dict[date, dict[str, Decimal]] = {}
    totals = {
        key: ZERO
        for key in (
            "revenue",
            "cost",
            "expense",
            "cash_in",
            "cash_out",
            "adjustment_net",
        )
    }
    before_effect, closing_effect = ZERO, ZERO

    def day_row(when: datetime):
        key = aware(when).astimezone(HK).date()
        return days.setdefault(key, {name: ZERO for name in totals})

    def recognize(category_id: str, amount: Decimal, when: datetime):
        if not start <= aware(when) < end:
            return
        kind = categories[category_id].business_kind
        totals[kind] += amount
        day_row(when)[kind] += amount
        category_amounts[category_id] = category_amounts.get(category_id, ZERO) + amount

    for transaction in transactions:
        cash_in, cash_out, adjustment = ZERO, ZERO, ZERO
        amount = transaction.amount
        if transaction.type == "income":
            cash_in = amount
        elif transaction.type == "expense":
            cash_out = amount
        elif transaction.type == "refund":
            original = transaction_by_id.get(transaction.related_transaction_id)
            if original is None:
                raise HTTPException(409, "退款缺少有效原交易，无法生成经营报表")
            if original.type == "expense":
                cash_in = amount
            else:
                cash_out = amount
            amount = -amount
        elif transaction.type == "adjustment":
            adjustment = (
                amount if transaction.adjustment_direction == "increase" else -amount
            )
        when = aware(transaction.occurred_at)
        effect = cash_in - cash_out + adjustment
        if when < start:
            before_effect += effect
        if when < end:
            closing_effect += effect
        if start <= when < end:
            row = day_row(when)
            for name, value in (
                ("cash_in", cash_in),
                ("cash_out", cash_out),
                ("adjustment_net", adjustment),
            ):
                totals[name] += value
                row[name] += value
        if transaction.id not in linked_ids and transaction.category_id:
            recognize(transaction.category_id, amount, when)

    for item in obligations:
        recognize(item.category_id, item.amount, item.recognized_at)

    outstanding = {"receivable": ZERO, "payable": ZERO}
    overdue = {"receivable": ZERO, "payable": ZERO}
    paid_by_item: dict[str, Decimal] = {}
    for link in links:
        transaction = transaction_by_id.get(link.transaction_id)
        if transaction is not None and aware(transaction.occurred_at) < end:
            paid_by_item[link.item_id] = (
                paid_by_item.get(link.item_id, ZERO) + transaction.amount
            )
    for item in obligations:
        if aware(item.recognized_at) < end:
            remaining = item.amount - paid_by_item.get(item.id, ZERO)
            outstanding[item.kind] += remaining
            if item.due_date < last:
                overdue[item.kind] += remaining
    accounts = balances(db, workspace_id)
    opening = sum((a.opening_balance for a in accounts), ZERO)
    cash_flow = totals["cash_in"] - totals["cash_out"]
    return BusinessReport(
        currency=currency,
        start_date=first,
        end_date=last,
        **totals,
        profit=totals["revenue"] - totals["cost"] - totals["expense"],
        operating_cash_flow=cash_flow,
        net_cash_flow=cash_flow + totals["adjustment_net"],
        opening_balance=opening + before_effect,
        closing_balance=opening + closing_effect,
        current_balance=sum((a.balance for a in accounts), ZERO),
        receivable_balance=outstanding["receivable"],
        payable_balance=outstanding["payable"],
        overdue_receivable=overdue["receivable"],
        overdue_payable=overdue["payable"],
        categories=[
            {
                "category_id": c.id,
                "name": c.name,
                "kind": c.business_kind,
                "amount": category_amounts[c.id],
            }
            for c in categories.values()
            if c.id in category_amounts
        ],
        daily=[
            BusinessDay(
                date=day,
                **row,
                profit=row["revenue"] - row["cost"] - row["expense"],
                net_cash_flow=row["cash_in"] - row["cash_out"] + row["adjustment_net"],
            )
            for day, row in sorted(days.items())
        ],
    )
