from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.finance import AccountPublic


class DashboardDay(BaseModel):
    date: date
    income: Decimal
    expense: Decimal
    net: Decimal
    cash_in: Decimal
    cash_out: Decimal
    adjustment_net: Decimal
    net_cash_flow: Decimal
    closing_balance: Decimal


class DashboardCategory(BaseModel):
    category_id: str
    name: str
    kind: str
    amount: Decimal


class DashboardPublic(BaseModel):
    workspace_type: Literal["personal", "business"]
    currency: str
    timezone: str = "Asia/Hong_Kong"
    start_date: date
    end_date: date
    basis: str
    formula: str
    has_activity: bool
    income: Decimal
    expense: Decimal
    net: Decimal
    cost: Decimal | None
    operating_expense: Decimal | None
    cash_in: Decimal
    cash_out: Decimal
    adjustment_net: Decimal
    net_cash_flow: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    current_balance: Decimal
    receivable_balance: Decimal | None
    payable_balance: Decimal | None
    accounts: list[AccountPublic]
    categories: list[DashboardCategory]
    daily: list[DashboardDay]
