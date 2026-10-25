from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.finance import MoneyValue


class ObligationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    kind: Literal["receivable", "payable"]
    category_id: str = Field(min_length=1, max_length=36)
    amount: MoneyValue = Field(gt=0)
    recognized_at: AwareDatetime
    due_date: date
    counterparty: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class ObligationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    category_id: str
    amount: Decimal
    currency: str
    recognized_at: datetime
    due_date: date
    counterparty: str
    description: str
    status: str
    version: int
    paid_amount: Decimal
    remaining_amount: Decimal
    settlement_status: Literal["unsettled", "partial", "settled", "voided"]
    is_overdue: bool

    @field_validator("recognized_at")
    @classmethod
    def aware_output(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SettlementCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=36)
    amount: MoneyValue = Field(gt=0)
    occurred_at: AwareDatetime
    version: int = Field(ge=1)


class BusinessDay(BaseModel):
    date: date
    revenue: Decimal
    cost: Decimal
    expense: Decimal
    profit: Decimal
    cash_in: Decimal
    cash_out: Decimal
    adjustment_net: Decimal
    net_cash_flow: Decimal


class BusinessCategory(BaseModel):
    category_id: str
    name: str
    kind: str
    amount: Decimal


class BusinessReport(BaseModel):
    currency: str
    start_date: date
    end_date: date
    timezone: str = "Asia/Hong_Kong"
    basis: str = (
        "直接收支按交易日期确认，应收应付按业务确认日期计入利润；其结算仅影响现金流。"
    )
    formula: str = "简化利润 = 营业收入 - 经营成本 - 经营费用"
    revenue: Decimal
    cost: Decimal
    expense: Decimal
    profit: Decimal
    cash_in: Decimal
    cash_out: Decimal
    adjustment_net: Decimal
    operating_cash_flow: Decimal
    net_cash_flow: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    current_balance: Decimal
    receivable_balance: Decimal
    payable_balance: Decimal
    overdue_receivable: Decimal
    overdue_payable: Decimal
    categories: list[BusinessCategory]
    daily: list[BusinessDay]
