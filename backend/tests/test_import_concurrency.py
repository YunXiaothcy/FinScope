from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Account, Category, ImportRow, Transaction, User, Workspace
from app.schemas.import_batch import ConfirmRequest, ImportMapping, PreviewRequest
from app.services import import_batch as service
from app.services.finance import begin_write, idempotent_create
from app.services.import_parser import parse_file


def test_concurrent_batches_post_same_transaction_once(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'import-concurrent.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            email="import-concurrent@example.com",
            password_hash="test-only",
            display_name="虚构导入",
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name="并发导入", type="personal", base_currency="CNY", created_by=user.id
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
        requests = []
        for index in range(2):
            content = f"日期,金额,无关列\n2026-10-01,10.25,{index}\n".encode()
            begin_write(db)
            batch = service.create_batch(
                db,
                workspace.id,
                user.id,
                "parallel.csv",
                content,
                parse_file("parallel.csv", content),
            )
            service.preview(
                db,
                workspace,
                batch,
                PreviewRequest(
                    version=batch.version,
                    mapping=ImportMapping(
                        account_id=account.id,
                        date_column=0,
                        amount_column=1,
                        default_expense_category_id=category.id,
                    ),
                ),
            )
            requests.append(
                (
                    batch.id,
                    ConfirmRequest(
                        version=batch.version,
                        row_ids=[service.batch_rows(db, batch)[0].id],
                    ),
                )
            )
            db.commit()
        workspace_id, user_id = workspace.id, user.id
    barrier = Barrier(2)

    def worker(index):
        batch_id, payload = requests[index]
        with Session(engine, expire_on_commit=False) as db:
            space = db.get(Workspace, workspace_id)
            barrier.wait(timeout=5)
            return idempotent_create(
                db,
                workspace_id,
                user_id,
                f"parallel-import-{index}",
                "import_confirm",
                payload,
                lambda: service.confirm(db, space, batch_id, user_id, payload),
                serializer=lambda batch: service.summary(db, batch).model_dump(
                    mode="json"
                ),
                context=batch_id,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert sum(result["counts"].get("imported", 0) for result in results) == 1
    assert sum(result["counts"].get("duplicate", 0) for result in results) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Transaction)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(ImportRow)
                .where(ImportRow.fingerprint.is_not(None))
            )
            == 1
        )
    engine.dispose()
