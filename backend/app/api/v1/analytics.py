from datetime import date

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.v1.workspaces import WorkspaceAccess, get_workspace_access
from app.db.session import get_db
from app.schemas.analytics import DashboardPublic
from app.services.analytics import dashboard

router = APIRouter(prefix="/workspaces/{workspace_id}/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=DashboardPublic)
def read_dashboard(
    response: Response,
    start_date: date | None = None,
    end_date: date | None = None,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return dashboard(db, access.workspace, start_date, end_date)
