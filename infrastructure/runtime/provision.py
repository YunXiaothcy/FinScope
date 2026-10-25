"""一次性引导专用非管理员角色；API 容器拿不到管理员凭据。"""

import os
from pathlib import Path

import psycopg
from app.core.config import database_configuration
from psycopg import sql
from sqlalchemy.engine import make_url


def provision():
    url = make_url(database_configuration())
    admin_password = (
        Path(os.environ["FINSCOPE_ADMIN_PASSWORD_FILE"])
        .read_text(encoding="utf-8")
        .rstrip("\r\n")
    )
    common = {
        "host": url.host,
        "port": url.port,
        "dbname": url.database,
        "connect_timeout": 5,
    }
    with psycopg.connect(
        **common, user="finscope_admin", password=admin_password, autocommit=True
    ) as connection:
        role = connection.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
            (url.username,),
        ).fetchone()
        if role and any(role):
            raise RuntimeError("Application database role has excessive privileges")
        if role is None:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(url.username), sql.Literal(url.password))
            )
        connection.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(url.database)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(url.database), sql.Identifier(url.username)
            )
        )
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(url.username)
            )
        )
    with psycopg.connect(
        **common, user=url.username, password=url.password
    ) as connection:
        connection.execute("SELECT 1")


if __name__ == "__main__":
    try:
        provision()
    except (psycopg.Error, RuntimeError, OSError, KeyError):
        raise SystemExit(
            "Database provisioning failed; check private configuration and role permissions"
        ) from None
    print("Dedicated application role is ready")
