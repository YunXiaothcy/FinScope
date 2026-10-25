from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.finance import new_id
from app.models.user import utc_now


class AnalysisLog(Base):
    __tablename__ = "ai_analysis_logs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "created_by", "request_key", name="uq_analysis_request"
        ),
        CheckConstraint(
            "status IN ('local','running','complete','fallback','expired')",
            name="ck_analysis_status",
        ),
        Index("ix_analysis_owner_created", "workspace_id", "created_by", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    selection: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="local")
    provider: Mapped[str] = mapped_column(String(32), default="local")
    target: Mapped[str] = mapped_column(String(500), default="")
    error_code: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
