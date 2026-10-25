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
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.finance import new_id
from app.models.user import utc_now


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_import_batches_id_workspace"),
        CheckConstraint(
            "status IN ('draft','committed','cancelled','expired')",
            name="ck_import_batches_status",
        ),
        Index("ix_import_batches_workspace_hash", "workspace_id", "file_hash"),
        Index("ix_import_batches_workspace_created", "workspace_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    filename: Mapped[str] = mapped_column(String(160))
    file_hash: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(8))
    sheet_name: Mapped[str] = mapped_column(String(80), default="")
    encoding: Mapped[str] = mapped_column(String(16), default="")
    headers: Mapped[list] = mapped_column(JSON)
    mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __mapper_args__: ClassVar[dict] = {"version_id_col": version}


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "workspace_id"],
            ["import_batches.id", "import_batches.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["transaction_id", "workspace_id"],
            ["transactions.id", "transactions.workspace_id"],
        ),
        UniqueConstraint("batch_id", "row_number", name="uq_import_rows_batch_number"),
        UniqueConstraint(
            "workspace_id", "fingerprint", name="uq_import_rows_workspace_fingerprint"
        ),
        UniqueConstraint("transaction_id", name="uq_import_rows_transaction"),
        CheckConstraint(
            "status IN ('uploaded','ready','error','duplicate','imported','skipped')",
            name="ck_import_rows_status",
        ),
        Index("ix_import_rows_workspace_batch", "workspace_id", "batch_id"),
        Index(
            "ix_import_rows_workspace_content", "workspace_id", "content_fingerprint"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    batch_id: Mapped[str] = mapped_column(String(36))
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[list | None] = mapped_column(JSON, nullable=True)
    parsing_errors: Mapped[list] = mapped_column(JSON, default=list)
    normalized: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
