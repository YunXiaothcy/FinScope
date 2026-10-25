import os
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import database_configuration

DEFAULT_DATABASE_URL = "sqlite:///./finscope_dev.db"
load_dotenv(Path(__file__).resolve().parents[3] / ".env")
database_url = database_configuration()

if os.getenv("FINSCOPE_ENV") == "production" and not database_url.startswith(
    "postgresql+psycopg://"
):
    raise RuntimeError("Production requires a PostgreSQL database URL")

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    hide_parameters=True,
    connect_args={"check_same_thread": False}
    if database_url.startswith("sqlite")
    else {"connect_timeout": 5},
)

if database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
