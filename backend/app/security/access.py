"""Recheck authorization after work performed outside a database transaction."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthSession, User, WorkspaceMember
from app.security.auth import AuthContext


def revalidate_write_access(db: Session, workspace_id: str, auth: AuthContext):
    # Select scalar columns: ORM identity maps may still contain pre-task state.
    row = db.execute(
        select(AuthSession.revoked_at, AuthSession.expires_at, User.is_active)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.id == auth.session.id, User.id == auth.user.id)
    ).one_or_none()
    if row is None:
        raise HTTPException(401, "登录已失效")
    revoked, expires, active = row
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if revoked or not active or expires <= datetime.now(UTC):
        raise HTTPException(401, "登录已失效")
    role = db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == auth.user.id,
        )
    )
    if role is None:
        raise HTTPException(404, "工作空间不存在")
    if role == "viewer":
        raise HTTPException(403, "当前成员仅有只读权限")
