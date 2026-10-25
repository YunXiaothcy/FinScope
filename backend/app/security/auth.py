import hashlib
import os
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import security_configuration
from app.db.session import get_db
from app.models import AuthSession, User
from app.security.limits import WindowLimiter

settings = security_configuration()
COOKIE_NAME = (
    "__Host-finscope_session" if settings["production"] else "finscope_session"
)
SESSION_AGE_SECONDS = 7 * 24 * 60 * 60
password_hasher = PasswordHasher(type=Type.ID)
allowed_origins = settings["origins"]
cookie_secure = settings["secure"]
if os.getenv("FINSCOPE_ENV") == "production" and not cookie_secure:
    raise RuntimeError("Production requires Secure session cookies")

_attempts = WindowLimiter(5, 300)
_ip_attempts = WindowLimiter(30, 300)
_password_attempts = WindowLimiter(5, 300)
_password_slots = threading.BoundedSemaphore(2)
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


def ensure_allowed_origin(request: Request) -> None:
    # 浏览器跨站提交必须带 Origin；无 Origin 的脚本请求仍需通过会话及 CSRF 校验。
    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="请求来源不被允许")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def check_login_limit(key: str) -> None:
    _attempts.consume(hashlib.sha256(key.encode()).hexdigest())


def clear_login_failures(key: str) -> None:
    _attempts.clear(hashlib.sha256(key.encode()).hexdigest())


def check_auth_ip_limit(request: Request) -> None:
    _ip_attempts.consume(request.client.host if request.client else "unknown")


def check_password_change_limit(user_id: str) -> None:
    _password_attempts.consume(user_id)


@contextmanager
def password_slot():
    if not _password_slots.acquire(blocking=False):
        raise HTTPException(429, "认证服务繁忙，请稍后重试")
    try:
        yield
    finally:
        _password_slots.release()


def hash_password(password: str) -> str:
    with password_slot():
        return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    with password_slot():
        try:
            return password_hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


def create_session(db: Session, user: User, response: Response) -> AuthSession:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_AGE_SECONDS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_AGE_SECONDS,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )
    return session


def get_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="请先登录")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == token_hash)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="登录已失效")
    session, user = row
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if session.revoked_at or expires_at <= datetime.now(UTC) or not user.is_active:
        raise HTTPException(status_code=401, detail="登录已失效")
    return AuthContext(user=user, session=session)


def require_csrf(
    request: Request, auth: AuthContext = Depends(get_auth)
) -> AuthContext:
    ensure_allowed_origin(request)
    submitted = request.headers.get("x-csrf-token", "")
    if not submitted.isascii() or not secrets.compare_digest(
        submitted, auth.session.csrf_token
    ):
        raise HTTPException(status_code=403, detail="CSRF 验证失败")
    return auth
