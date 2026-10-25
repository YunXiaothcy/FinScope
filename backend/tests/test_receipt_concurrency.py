from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Account, Category, FundsMovement, Transaction, User, Workspace
from app.schemas.receipt import ReceiptConfirm
from app.services import receipts as service
from app.services.finance import begin_write, idempotent_create
from app.services.receipt_images import SafeImage


def test_concurrent_confirmation_creates_one_receipt_transaction(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'receipt-concurrent.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            email="parallel-receipt@example.com",
            password_hash="test-only",
            display_name="虚构票据",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name="并发票据", type="personal", base_currency="CNY", created_by=user.id
        )
        db.add(workspace)
        db.flush()
        account = Account(
            workspace_id=workspace.id, name="账户", type="bank", currency="CNY"
        )
        category = Category(
            workspace_id=workspace.id,
            name="餐饮",
            type="expense",
            business_kind="expense",
        )
        db.add_all([account, category])
        db.commit()
        begin_write(db)
        receipt = service.create(
            db,
            workspace.id,
            user.id,
            "test.jpg",
            SafeImage(b"test-image", "a" * 64, 10, 10),
        )
        receipt.reviewed_payload = {
            "reviewed": True,
            "currency": "CNY",
            "currency_hint_corrected": False,
            "transaction": {
                "account_id": account.id,
                "category_id": category.id,
                "type": "expense",
                "amount": "10.25",
                "occurred_at": "2026-10-24T04:00:00Z",
            },
        }
        db.commit()
        workspace_id, user_id, receipt_id = workspace.id, user.id, receipt.id
        payload = ReceiptConfirm(version=receipt.version, reviewed=True)
    barrier = Barrier(2)

    def worker(index):
        with Session(engine, expire_on_commit=False) as db:
            space = db.get(Workspace, workspace_id)
            barrier.wait(timeout=5)
            try:
                idempotent_create(
                    db,
                    workspace_id,
                    user_id,
                    f"receipt-concurrent-{index}",
                    "receipt_confirm",
                    payload,
                    lambda: service.confirm(db, space, receipt_id, user_id, payload),
                    serializer=lambda item: service.summary(item).model_dump(
                        mode="json"
                    ),
                    context=receipt_id,
                )
                return "confirmed"
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert sorted(results) == ["confirmed", "conflict"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert db.scalar(select(func.count()).select_from(FundsMovement)) == 1
    engine.dispose()
