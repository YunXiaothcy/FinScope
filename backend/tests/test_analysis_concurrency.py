import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from test_analysis import ask, base

from app.db.session import get_db
from app.main import app
from app.services import analysis as service

pytest_plugins = ["test_finance"]


@pytest.fixture
def file_ledger(ledger, tmp_path):
    service._attempts.clear()
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    source = db.get_bind().raw_connection()
    path = tmp_path / "analysis-concurrent.db"
    with sqlite3.connect(path) as target:
        source.driver_connection.backup(target)
    source.close()
    generator.close()
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    def override():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield ledger
    engine.dispose()
    service._attempts.clear()


def test_simultaneous_identical_question_has_one_log(file_ledger):
    barrier = Barrier(2)

    def worker(_):
        barrier.wait(timeout=5)
        return ask(file_ledger, key="parallel-question-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(worker, range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert len(file_ledger["client"].get(base(file_ledger)).json()) == 1
