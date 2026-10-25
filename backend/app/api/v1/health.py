from functools import lru_cache
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health() -> dict[str, str]:
    """用于本地开发和部署探针的最小健康检查。"""
    return {"status": "ok"}


@lru_cache
def migration_heads() -> set[str]:
    return set(
        ScriptDirectory(
            str(Path(__file__).resolve().parents[3] / "alembic")
        ).get_heads()
    )


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    try:
        revisions = set(
            db.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
        if revisions != migration_heads():
            raise ValueError("migration_mismatch")
        if db.bind.dialect.name == "postgresql" and db.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        ):
            raise ValueError("admin_role")
    except (SQLAlchemyError, ValueError):
        db.rollback()
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready"},
            headers={"Cache-Control": "no-store"},
        )
    return {"status": "ready"}
