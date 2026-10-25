from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.db.money import validate_money

MoneyValue = Annotated[Decimal, BeforeValidator(validate_money)]


class AccountCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    type: Literal["cash", "bank", "ewallet", "credit"] = "bank"
    opening_balance: MoneyValue = Decimal("0.00")


class AccountUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    is_active: bool


class AccountPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    type: str
    currency: str
    opening_balance: Decimal
    balance: Decimal
    is_active: bool


class CategoryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    type: Literal["income", "expense"]
    business_kind: Literal["revenue", "cost", "expense"] | None = None


class CategoryPublic(CategoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    business_kind: Literal["revenue", "cost", "expense"]


class TransactionCreate(BaseModel):
    account_id: str = Field(max_length=36)
    category_id: str | None = Field(default=None, max_length=36)
    type: Literal["income", "expense", "refund", "adjustment"]
    amount: MoneyValue = Field(gt=0)
    occurred_at: AwareDatetime
    description: str = Field(default="", max_length=500)
    merchant_name: str = Field(default="", max_length=120)
    related_transaction_id: str | None = Field(default=None, max_length=36)
    adjustment_direction: Literal["increase", "decrease"] | None = None

    @model_validator(mode="after")
    def check_semantics(self):
        if self.type in {"income", "expense"} and not self.category_id:
            raise ValueError("收支必须选择分类")
        if (self.type == "refund") != bool(self.related_transaction_id):
            raise ValueError("退款必须关联原交易")
        if (self.type == "adjustment") != bool(self.adjustment_direction):
            raise ValueError("调整必须指定增减方向")
        if self.type == "adjustment" and self.category_id:
            raise ValueError("余额调整不计入分类收支")
        return self


class TransactionEdit(TransactionCreate):
    version: int = Field(ge=1)


class TimedPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def aware_output(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class TransactionPublic(TimedPublic):
    id: str
    account_id: str
    category_id: str | None
    type: str
    amount: Decimal
    currency: str
    description: str
    merchant_name: str
    status: str
    related_transaction_id: str | None
    adjustment_direction: str | None
    version: int
    source: str = "manual"


class TransferCreate(BaseModel):
    from_account_id: str = Field(max_length=36)
    to_account_id: str = Field(max_length=36)
    amount: MoneyValue = Field(gt=0)
    occurred_at: AwareDatetime
    description: str = Field(default="", max_length=500)


class TransferPublic(TimedPublic):
    id: str
    from_account_id: str
    to_account_id: str
    amount: Decimal
    currency: str
    description: str
    status: str
    version: int


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class TransactionsPage(BaseModel):
    items: list[TransactionPublic]
    total: int
    limit: int
    offset: int


class CategoryTotal(BaseModel):
    category_id: str
    name: str
    type: str
    amount: Decimal


class SummaryPublic(BaseModel):
    currency: str
    start_date: date
    end_date: date
    timezone: str = "Asia/Hong_Kong"
    income: Decimal
    expense: Decimal
    net: Decimal
    adjustment_net: Decimal
    cash_flow: Decimal
    total_balance: Decimal
    categories: list[CategoryTotal]


class AuditPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    entity_type: str
    entity_id: str
    user_id: str
    before: dict | None
    after: dict | None
    created_at: datetime


class MovementPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    transaction_id: str | None
    transfer_id: str | None
    amount: Decimal
    reverses_id: str | None
    created_at: datetime
