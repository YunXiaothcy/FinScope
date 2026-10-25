from datetime import date as Date
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.finance import MoneyValue, TransactionCreate


class ReceiptCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: MoneyValue | None = Field(default=None, gt=0)
    date: Date | None = None
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(?::\d{2})?$")
    merchant_name: str | None = Field(default=None, max_length=120)
    category_hint: str | None = Field(default=None, max_length=80)
    transaction_type: Literal["income", "expense"] | None = None
    currency: Literal["CNY", "HKD", "USD", "EUR"] | None = None
    confidence: dict[str, Literal["unknown", "low", "medium"]] = Field(
        default_factory=dict, max_length=8
    )
    warnings: list[Annotated[str, Field(max_length=180)]] = Field(
        default_factory=list, max_length=10
    )

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        if value is not None and not 1970 <= value.year <= 2100:
            raise ValueError("日期超出允许范围")
        return value

    @field_validator("time")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            datetime.strptime(value, "%H:%M:%S" if len(value) == 8 else "%H:%M")
        return value


class ReceiptSummary(BaseModel):
    id: str
    filename: str
    status: str
    ocr_status: str
    version: int
    created_at: datetime
    expires_at: datetime
    transaction_id: str | None


class ReceiptPublic(ReceiptSummary):
    media_type: str
    byte_size: int
    width: int
    height: int
    image_available: bool
    ocr_provider: str
    ocr_text: str | None
    extraction_provider: str
    candidates: ReceiptCandidates | None
    reviewed_payload: dict | None
    notices: list[str]
    external_text_preview: str


class StructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=12000)
    use_ai: bool = False
    consent: bool = False
    consent_target: str | None = Field(default=None, max_length=400)


class ReceiptReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    reviewed: Literal[True]
    currency: Literal["CNY", "HKD", "USD", "EUR"]
    currency_hint_corrected: bool = False
    transaction: TransactionCreate


class ReceiptConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    reviewed: Literal[True]
