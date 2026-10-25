from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Account,
    BusinessSettlement,
    Category,
    ReceivablePayable,
    Transaction,
    User,
    Workspace,
)
from app.schemas.business import SettlementCreate
from app.services.business import public_item, settle_item
from app.services.finance import idempotent_create


def test_concurrent_settlements_cannot_overpay(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'settlements.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            email="parallel-business@example.com",
            password_hash="test-only",
            display_name="虚构商户",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name="并发商户", type="business", base_currency="CNY", created_by=user.id
        )
        db.add(workspace)
        db.flush()
        account = Account(
            workspace_id=workspace.id,
            name="测试账户",
            type="bank",
            currency="CNY",
            opening_balance="0.00",
        )
        category = Category(workspace_id=workspace.id, name="销售", type="income")
        db.add_all([account, category])
        db.flush()
        item = ReceivablePayable(
            workspace_id=workspace.id,
            category_id=category.id,
            kind="receivable",
            amount="100.00",
            currency="CNY",
            recognized_at=datetime(2026, 10, 1, tzinfo=UTC),
            due_date=date(2026, 10, 31),
            counterparty="虚构客户",
            created_by=user.id,
        )
        db.add(item)
        db.commit()
        workspace_id, user_id, account_id, item_id = (
            workspace.id,
            user.id,
            account.id,
            item.id,
        )
    payload = SettlementCreate(
        account_id=account_id,
        amount="60.00",
        occurred_at=datetime(2026, 10, 2, tzinfo=UTC),
        version=1,
    )
    barrier = Barrier(2)

    def worker(_):
        with Session(engine, expire_on_commit=False, autoflush=False) as db:
            barrier.wait(timeout=5)
            try:
                idempotent_create(
                    db,
                    workspace_id,
                    user_id,
                    str(uuid4()),
                    "business_settlement",
                    payload,
                    lambda: settle_item(db, workspace_id, item_id, user_id, payload),
                    context=item_id,
                )
                return 201
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(worker, range(2))) == [201, 409]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert db.scalar(select(func.count()).select_from(BusinessSettlement)) == 1
        assert (
            public_item(db, db.get(ReceivablePayable, item_id)).remaining_amount == 40
        )
    engine.dispose()
