from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def test_business_upgrade_preserves_categories_and_posted_money(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("FINSCOPE_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0002_personal_finance")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,display_name,is_active,created_at,updated_at) VALUES ('u','legacy@example.com','test-only','虚构用户',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspaces (id,name,type,base_currency,created_by,created_at) VALUES ('w','旧商户','business','CNY','u',CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO accounts (id,workspace_id,name,type,currency,opening_balance,is_active,created_at) VALUES ('a','w','账户','bank','CNY',100000,1,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO categories (id,workspace_id,name,type,created_at) VALUES ('revenue','w','旧收入','income',CURRENT_TIMESTAMP),('expense','w','旧支出','expense',CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO transactions (id,workspace_id,account_id,category_id,type,amount,currency,occurred_at,description,merchant_name,source,status,created_by,version,created_at,updated_at) VALUES ('t','w','a','expense','expense',1234,'CNY',CURRENT_TIMESTAMP,'虚构支出','','manual','posted','u',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO funds_movements (id,workspace_id,account_id,transaction_id,amount,created_at) VALUES ('m','w','a','t',-1234,CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        categories = dict(
            connection.execute(text("SELECT id,business_kind FROM categories")).all()
        )
        assert categories == {"revenue": "revenue", "expense": "expense"}
        assert (
            connection.scalar(text("SELECT amount FROM transactions WHERE id='t'"))
            == 1234
        )
        assert (
            connection.scalar(text("SELECT amount FROM funds_movements WHERE id='m'"))
            == -1234
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    command.downgrade(config, "0002_personal_finance")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT name FROM categories WHERE id='expense'"))
            == "旧支出"
        )
        assert connection.scalar(text("SELECT COUNT(*) FROM transactions")) == 1
    engine.dispose()
