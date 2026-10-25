from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def test_initial_migration_upgrade_and_downgrade(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("FINSCOPE_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "users",
        "auth_sessions",
        "workspaces",
        "workspace_members",
        "accounts",
        "categories",
        "transactions",
        "transfers",
        "funds_movements",
        "idempotency_requests",
        "audit_logs",
        "receivables_payables",
        "business_settlements",
    } <= tables
    assert {"alembic_version"} <= tables
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(database_url)
    assert "users" not in inspect(engine).get_table_names()
    engine.dispose()


def test_stage_one_data_survives_stage_two_upgrade(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    monkeypatch.setenv("FINSCOPE_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0001_accounts_workspaces")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,display_name,is_active,created_at,updated_at) VALUES ('u','existing@example.com','test-only','保留用户',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspaces (id,name,type,base_currency,created_by,created_at) VALUES ('w','保留空间','personal','CNY','u',CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT display_name FROM users WHERE id='u'"))
            == "保留用户"
        )
        assert (
            connection.scalar(text("SELECT name FROM workspaces WHERE id='w'"))
            == "保留空间"
        )
    assert "funds_movements" in inspect(engine).get_table_names()
    engine.dispose()
