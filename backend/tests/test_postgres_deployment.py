"""显式设置隔离的 PostgreSQL 测试库才运行；永不使用正式数据库。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_business import cash, obligation, settle
from test_finance import post

from alembic import command
from app.db.session import get_db
from app.main import app
from app.schemas.finance import TransactionCreate
from app.services.finance import idempotent_create, post_transaction

pytest_plugins = ["test_finance", "test_business"]


@pytest.fixture
def client(monkeypatch):
    value = os.getenv("FINSCOPE_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("requires an isolated real PostgreSQL test database")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg" or not url.database.startswith(
        "finscope_test_"
    ):
        pytest.fail(
            "PostgreSQL test target must be a dedicated finscope_test_ database"
        )
    schema = "test_" + uuid4().hex
    base_engine = create_engine(url, hide_parameters=True)
    with base_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv(
        "FINSCOPE_DATABASE_URL", scoped.render_as_string(hide_password=False)
    )
    config = Config("alembic.ini")
    engine = create_engine(scoped, hide_parameters=True)
    previous = dict(app.dependency_overrides)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert not connection.scalar(
                text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
            )

        def override():
            with Session(engine, autoflush=False, expire_on_commit=False) as db:
                yield db

        app.dependency_overrides[get_db] = override
        with TestClient(app) as test_client:
            yield test_client
        command.check(config)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()
        with base_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base_engine.dispose()


def test_real_postgres_numeric_refunds_transfer_and_idempotency(ledger):
    for value in ("0.10", "0.20"):
        assert post(ledger, "income", value).status_code == 201
    expense = post(
        ledger,
        amount="8.88",
        key="postgres-expense-key",
        occurred_at="2026-10-24T04:00:00Z",
    )
    assert expense.status_code == 201
    assert (
        post(
            ledger,
            amount="8.88",
            key="postgres-expense-key",
            occurred_at="2026-10-24T04:00:00Z",
        ).json()
        == expense.json()
    )
    assert (
        post(
            ledger, "refund", "0.88", related_transaction_id=expense.json()["id"]
        ).status_code
        == 201
    )
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/transfers",
            headers={**ledger["headers"], "Idempotency-Key": "postgres-transfer-key"},
            json={
                "from_account_id": ledger["accounts"][0]["id"],
                "to_account_id": ledger["accounts"][1]["id"],
                "amount": "2.00",
                "occurred_at": "2026-10-24T04:00:00Z",
            },
        )
        .status_code
        == 201
    )
    data = ledger["client"].get(f"{ledger['base']}/summary").json()
    assert (data["income"], data["expense"], data["total_balance"]) == (
        "0.30",
        "8.00",
        "92.30",
    )
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        assert db.scalar(
            text(
                "SELECT amount FROM transactions WHERE type='income' ORDER BY amount LIMIT 1"
            )
        ) == Decimal("0.10")
        info = db.execute(
            text(
                "SELECT data_type,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='transactions' AND column_name='amount'"
            )
        ).one()
        assert tuple(info) == ("numeric", 18, 2)
    finally:
        generator.close()


def test_real_postgres_business_profit_and_receivable_settlement(business):
    cash(business, "revenue", "100.00")
    cash(business, "cost", "40.00")
    cash(business, "expense", "10.00")
    item = obligation(business, amount="200.00")
    assert settle(business, item, "50.00").status_code == 201
    report = (
        business["client"]
        .get(
            f"{business['base']}/business/report?start_date=2026-10-01&end_date=2026-10-24"
        )
        .json()
    )
    assert (report["profit"], report["receivable_balance"]) == ("250.00", "150.00")
    assert report["net_cash_flow"] == "100.00"


def test_real_postgres_concurrent_retry_uses_one_financial_transaction(ledger):
    generator = app.dependency_overrides[get_db]()
    session = next(generator)
    engine = session.get_bind()
    user_id = ledger["client"].get("/api/v1/auth/session").json()["user"]["id"]
    generator.close()
    payload = TransactionCreate(
        account_id=ledger["accounts"][0]["id"],
        category_id=ledger["categories"]["expense"],
        type="expense",
        amount="7.89",
        occurred_at=datetime.now(UTC),
    )
    barrier = Barrier(2)

    def worker(_):
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            barrier.wait(timeout=5)
            return idempotent_create(
                db,
                ledger["space"]["id"],
                user_id,
                "postgres-same-request",
                "transaction",
                payload,
                lambda: post_transaction(db, ledger["space"]["id"], user_id, payload),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert results[0] == results[1]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transactions")) == 1
        assert connection.scalar(text("SELECT count(*) FROM funds_movements")) == 1
