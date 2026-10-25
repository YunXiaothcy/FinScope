from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.finance import new_id
from app.models.user import utc_now


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["transaction_id", "workspace_id"],
            ["transactions.id", "transactions.workspace_id"],
        ),
        UniqueConstraint("transaction_id", name="uq_attachments_transaction"),
        CheckConstraint(
            "status IN ('draft','confirmed','cancelled','expired')",
            name="ck_attachments_status",
        ),
        CheckConstraint(
            "ocr_status IN ('pending','ready','failed')",
            name="ck_attachments_ocr_status",
        ),
        Index("ix_attachments_workspace_hash", "workspace_id", "image_hash"),
        Index("ix_attachments_workspace_created", "workspace_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    filename: Mapped[str] = mapped_column(String(160))
    image_hash: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32), default="image/jpeg")
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    image_data: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, deferred=True
    )
    status: Mapped[str] = mapped_column(String(16), default="draft")
    ocr_status: Mapped[str] = mapped_column(String(16), default="pending")
    ocr_provider: Mapped[str] = mapped_column(String(32), default="")
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_provider: Mapped[str] = mapped_column(String(32), default="")
    candidates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewed_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notices: Mapped[list] = mapped_column(JSON, default=list)
    transaction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __mapper_args__: ClassVar[dict] = {"version_id_col": version}
