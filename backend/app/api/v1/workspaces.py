from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace import (
    MemberCreate,
    MemberPublic,
    WorkspaceCreate,
    WorkspacePublic,
)
from app.security.auth import AuthContext, get_auth, require_csrf

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    member: WorkspaceMember


def workspace_public(workspace: Workspace, role: str) -> WorkspacePublic:
    return WorkspacePublic(
        id=workspace.id,
        name=workspace.name,
        type=workspace.type,
        base_currency=workspace.base_currency,
        role=role,
        created_at=workspace.created_at,
    )


def get_workspace_access(
    workspace_id: str,
    auth: AuthContext = Depends(get_auth),
    db: Session = Depends(get_db),
) -> WorkspaceAccess:
    # 只从已认证用户的成员关系取得工作空间，不能仅相信路径中的 ID。
    row = db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMember.user_id == auth.user.id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return WorkspaceAccess(workspace=row[0], member=row[1])


@router.get("", response_model=list[WorkspacePublic])
def list_workspaces(
    auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)
) -> list[WorkspacePublic]:
    rows = db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == auth.user.id)
        .order_by(Workspace.created_at.desc())
    ).all()
    return [workspace_public(workspace, member.role) for workspace, member in rows]


@router.post("", response_model=WorkspacePublic, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> WorkspacePublic:
    workspace = Workspace(
        name=payload.name,
        type=payload.type,
        base_currency=payload.base_currency,
        created_by=auth.user.id,
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=auth.user.id, role="owner")
    )
    db.commit()
    db.refresh(workspace)
    return workspace_public(workspace, "owner")


@router.get("/{workspace_id}", response_model=WorkspacePublic)
def get_workspace(
    access: WorkspaceAccess = Depends(get_workspace_access),
) -> WorkspacePublic:
    return workspace_public(access.workspace, access.member.role)


@router.get("/{workspace_id}/members", response_model=list[MemberPublic])
def list_members(
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
) -> list[MemberPublic]:
    rows = db.execute(
        select(User, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == access.workspace.id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    return [
        MemberPublic(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=member.role,
        )
        for user, member in rows
    ]


@router.post("/{workspace_id}/members", response_model=MemberPublic, status_code=201)
def add_member(
    payload: MemberCreate,
    access: WorkspaceAccess = Depends(get_workspace_access),
    _auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> MemberPublic:
    if access.member.role != "owner":
        raise HTTPException(status_code=403, detail="只有所有者可以添加成员")
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None:
        raise HTTPException(status_code=404, detail="此用户尚未注册")
    member = WorkspaceMember(
        workspace_id=access.workspace.id, user_id=user.id, role=payload.role
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户已在此工作空间") from exc
    return MemberPublic(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
    )
