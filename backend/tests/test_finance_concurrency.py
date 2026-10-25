from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Account, Category, FundsMovement, Transaction, User, Workspace
from app.schemas.finance import TransactionCreate
from app.services.finance import balances, idempotent_create, post_transaction


def test_concurrent_retries_and_refunds_are_serialized(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'parallel.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            email="parallel@example.com",
            password_hash="test-only",
            display_name="虚构用户",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name="并发测试", type="personal", base_currency="CNY", created_by=user.id
        )
        db.add(workspace)
        db.flush()
        account = Account(
            workspace_id=workspace.id,
            name="测试账户",
            type="bank",
            currency="CNY",
            opening_balance="100.00",
        )
        category = Category(workspace_id=workspace.id, name="支出", type="expense")
        db.add_all([account, category])
        db.commit()
        workspace_id, user_id, account_id, category_id = (
            workspace.id,
            user.id,
            account.id,
            category.id,
        )

    original = TransactionCreate(
        account_id=account_id,
        category_id=category_id,
        type="expense",
        amount="100.00",
        occurred_at=datetime.now(UTC),
    )
    key = str(uuid4())
    barrier = Barrier(2)

    def create_expense(_):
        with Session(engine, expire_on_commit=False, autoflush=False) as db:
            barrier.wait(timeout=5)
            return idempotent_create(
                db,
                workspace_id,
                user_id,
                key,
                "transaction",
                original,
                lambda: post_transaction(db, workspace_id, user_id, original),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_expense, range(2)))
    assert results[0] == results[1]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert db.scalar(select(func.count()).select_from(FundsMovement)) == 1

    refund = TransactionCreate(
        account_id=account_id,
        type="refund",
        amount="60.00",
        occurred_at=datetime.now(UTC),
        related_transaction_id=results[0]["id"],
    )
    barrier = Barrier(2)

    def create_refund(_):
        with Session(engine, expire_on_commit=False, autoflush=False) as db:
            barrier.wait(timeout=5)
            try:
                idempotent_create(
                    db,
                    workspace_id,
                    user_id,
                    str(uuid4()),
                    "transaction",
                    refund,
                    lambda: post_transaction(db, workspace_id, user_id, refund),
                )
                return 201
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(create_refund, range(2)))
    assert sorted(statuses) == [201, 422]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 2
        assert db.scalar(select(func.count()).select_from(FundsMovement)) == 2
    engine.dispose()
    # 模拟服务退出后重新打开文件，账目必须仍在磁盘上。
    reopened = create_engine(f"sqlite:///{(tmp_path / 'parallel.db').as_posix()}")
    with Session(reopened) as db:
        assert balances(db, workspace_id)[0].balance == Decimal("60.00")
    reopened.dispose()
