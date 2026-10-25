from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
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
from app.db.money import ZERO, Money
from app.models.user import utc_now


def new_id() -> str:
    return str(uuid4())


def default_business_kind(context) -> str:
    return (
        "revenue"
        if context.get_current_parameters().get("type") == "income"
        else "expense"
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_accounts_id_workspace"),
        UniqueConstraint("workspace_id", "name", name="uq_accounts_workspace_name"),
        CheckConstraint(
            "type IN ('cash','bank','ewallet','credit')", name="ck_accounts_type"
        ),
        Index("ix_accounts_workspace", "workspace_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    opening_balance: Mapped[Decimal] = mapped_column(Money(), default=ZERO)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_categories_id_workspace"),
        UniqueConstraint(
            "workspace_id", "type", "name", name="uq_categories_workspace_type_name"
        ),
        CheckConstraint("type IN ('income','expense')", name="ck_categories_type"),
        CheckConstraint(
            "(type = 'income' AND business_kind = 'revenue') OR (type = 'expense' AND business_kind IN ('cost','expense'))",
            name="ck_categories_business_kind",
        ),
        Index("ix_categories_workspace", "workspace_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(16))
    business_kind: Mapped[str] = mapped_column(
        String(16), default=default_business_kind, server_default="expense"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_transactions_id_workspace"),
        ForeignKeyConstraint(
            ["account_id", "workspace_id"], ["accounts.id", "accounts.workspace_id"]
        ),
        ForeignKeyConstraint(
            ["category_id", "workspace_id"],
            ["categories.id", "categories.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["related_transaction_id", "workspace_id"],
            ["transactions.id", "transactions.workspace_id"],
        ),
        CheckConstraint("amount > 0", name="ck_transactions_amount"),
        CheckConstraint(
            "type IN ('income','expense','refund','adjustment')",
            name="ck_transactions_type",
        ),
        CheckConstraint("status IN ('posted','voided')", name="ck_transactions_status"),
        CheckConstraint(
            "adjustment_direction IS NULL OR adjustment_direction IN ('increase','decrease')",
            name="ck_transactions_direction",
        ),
        CheckConstraint(
            "(type = 'refund' AND related_transaction_id IS NOT NULL) OR (type <> 'refund' AND related_transaction_id IS NULL)",
            name="ck_transactions_refund_link",
        ),
        CheckConstraint(
            "(type = 'adjustment' AND adjustment_direction IS NOT NULL AND category_id IS NULL) OR (type <> 'adjustment' AND adjustment_direction IS NULL AND category_id IS NOT NULL)",
            name="ck_transactions_category_direction",
        ),
        Index("ix_transactions_workspace_date", "workspace_id", "occurred_at"),
        Index("ix_transactions_related", "related_transaction_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    account_id: Mapped[str] = mapped_column(String(36))
    category_id: Mapped[str | None] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money())
    currency: Mapped[str] = mapped_column(String(3))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(500), default="")
    merchant_name: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="posted")
    related_transaction_id: Mapped[str | None] = mapped_column(String(36))
    adjustment_direction: Mapped[str | None] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    __mapper_args__: ClassVar[dict] = {"version_id_col": version}


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_transfers_id_workspace"),
        ForeignKeyConstraint(
            ["from_account_id", "workspace_id"],
            ["accounts.id", "accounts.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["to_account_id", "workspace_id"], ["accounts.id", "accounts.workspace_id"]
        ),
        CheckConstraint("amount > 0", name="ck_transfers_amount"),
        CheckConstraint(
            "from_account_id <> to_account_id", name="ck_transfers_different_accounts"
        ),
        CheckConstraint("status IN ('posted','voided')", name="ck_transfers_status"),
        Index("ix_transfers_workspace_date", "workspace_id", "occurred_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    from_account_id: Mapped[str] = mapped_column(String(36))
    to_account_id: Mapped[str] = mapped_column(String(36))
    amount: Mapped[Decimal] = mapped_column(Money())
    currency: Mapped[str] = mapped_column(String(3))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="posted")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    __mapper_args__: ClassVar[dict] = {"version_id_col": version}


class FundsMovement(Base):
    __tablename__ = "funds_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "workspace_id"], ["accounts.id", "accounts.workspace_id"]
        ),
        ForeignKeyConstraint(
            ["transaction_id", "workspace_id"],
            ["transactions.id", "transactions.workspace_id"],
        ),
        ForeignKeyConstraint(
            ["transfer_id", "workspace_id"], ["transfers.id", "transfers.workspace_id"]
        ),
        CheckConstraint("amount <> 0", name="ck_movements_nonzero"),
        CheckConstraint(
            "(transaction_id IS NOT NULL AND transfer_id IS NULL) OR (transaction_id IS NULL AND transfer_id IS NOT NULL)",
            name="ck_movements_one_source",
        ),
        Index("ix_movements_workspace_account", "workspace_id", "account_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    account_id: Mapped[str] = mapped_column(String(36))
    transaction_id: Mapped[str | None] = mapped_column(String(36))
    transfer_id: Mapped[str | None] = mapped_column(String(36))
    amount: Mapped[Decimal] = mapped_column(Money())
    reverses_id: Mapped[str | None] = mapped_column(
        ForeignKey("funds_movements.id"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class IdempotencyRequest(Base):
    __tablename__ = "idempotency_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "key", name="uq_idempotency_scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(36))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_workspace_date", "workspace_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(36))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
