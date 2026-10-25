from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  Register model metadata for migrations.
from alembic import context
from app.core.config import database_configuration
from app.db.base import Base
from app.db.money import Money

config = context.config
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", database_configuration().replace("%", "%%"))
target_metadata = Base.metadata


def render_item(type_, obj, _context):
    if type_ == "type" and isinstance(obj, Money):
        return 'sa.Numeric(18, 2).with_variant(sa.BigInteger(), "sqlite")'
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
