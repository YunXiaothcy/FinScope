from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuthSession, User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RegisterRequest,
    UserPublic,
)
from app.security.auth import (
    COOKIE_NAME,
    DUMMY_PASSWORD_HASH,
    AuthContext,
    check_auth_ip_limit,
    check_login_limit,
    check_password_change_limit,
    clear_login_failures,
    cookie_secure,
    create_session,
    ensure_allowed_origin,
    get_auth,
    hash_password,
    require_csrf,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    ensure_allowed_origin(request)
    check_auth_ip_limit(request)
    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="邮箱已注册") from exc
    db.refresh(user)
    session = create_session(db, user, response)
    return AuthResponse(user=user, csrf_token=session.csrf_token)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    ensure_allowed_origin(request)
    check_auth_ip_limit(request)
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{str(payload.email).lower()}"
    check_login_limit(key)
    user = db.scalar(select(User).where(User.email == str(payload.email)))
    valid_password = verify_password(
        payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if user is None or not user.is_active or not valid_password:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    clear_login_failures(key)
    session = create_session(db, user, response)
    return AuthResponse(user=user, csrf_token=session.csrf_token)


@router.get("/session", response_model=AuthResponse)
def current_session(auth: AuthContext = Depends(get_auth)) -> AuthResponse:
    return AuthResponse(user=auth.user, csrf_token=auth.session.csrf_token)


@router.patch("/profile", response_model=UserPublic)
def update_profile(
    payload: ProfileUpdate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> UserPublic:
    auth.user.display_name = payload.display_name
    db.commit()
    db.refresh(auth.user)
    return UserPublic.model_validate(auth.user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    check_password_change_limit(auth.user.id)
    if not verify_password(payload.current_password, auth.user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误")
    auth.user.password_hash = hash_password(payload.new_password)
    # 改密后撤销其他设备的会话，当前设备可继续完成操作。
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == auth.user.id, AuthSession.id != auth.session.id)
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    auth.session.revoked_at = datetime.now(UTC)
    db.commit()
    response.delete_cookie(
        COOKIE_NAME, path="/", secure=cookie_secure, httponly=True, samesite="lax"
    )
