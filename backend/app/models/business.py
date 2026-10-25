from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    Date,
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
from app.db.money import Money
from app.models.finance import new_id
from app.models.user import utc_now


class ReceivablePayable(Base):
    __tablename__ = "receivables_payables"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_obligations_id_workspace"),
        ForeignKeyConstraint(
            ["category_id", "workspace_id"],
            ["categories.id", "categories.workspace_id"],
        ),
        CheckConstraint("kind IN ('receivable','payable')", name="ck_obligations_kind"),
        CheckConstraint("status IN ('posted','voided')", name="ck_obligations_status"),
        CheckConstraint("amount > 0", name="ck_obligations_amount"),
        Index("ix_obligations_workspace_date", "workspace_id", "recognized_at"),
        Index("ix_obligations_workspace_due", "workspace_id", "due_date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    category_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money())
    currency: Mapped[str] = mapped_column(String(3))
    recognized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date] = mapped_column(Date)
    counterparty: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="posted")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    __mapper_args__: ClassVar[dict] = {"version_id_col": version}


class BusinessSettlement(Base):
    __tablename__ = "business_settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["receivables_payables.id", "receivables_payables.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["transaction_id", "workspace_id"],
            ["transactions.id", "transactions.workspace_id"],
        ),
        UniqueConstraint("transaction_id", name="uq_settlements_transaction"),
        Index("ix_settlements_workspace_item", "workspace_id", "item_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    item_id: Mapped[str] = mapped_column(String(36))
    transaction_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
