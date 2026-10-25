from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImportMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    account_id: str = Field(min_length=1, max_length=36)
    source_name: str = Field(default="账单导入", min_length=1, max_length=80)
    date_column: int = Field(ge=0, le=39)
    amount_column: int = Field(ge=0, le=39)
    type_column: int | None = Field(default=None, ge=0, le=39)
    category_column: int | None = Field(default=None, ge=0, le=39)
    description_column: int | None = Field(default=None, ge=0, le=39)
    merchant_column: int | None = Field(default=None, ge=0, le=39)
    external_id_column: int | None = Field(default=None, ge=0, le=39)
    currency_column: int | None = Field(default=None, ge=0, le=39)
    fixed_type: Literal["income", "expense"] = "expense"
    date_format: Literal["ymd", "dmy", "mdy"] = "ymd"
    default_income_category_id: str | None = Field(default=None, max_length=36)
    default_expense_category_id: str | None = Field(default=None, max_length=36)
    category_map: dict[str, str] = Field(default_factory=dict, max_length=1000)
    type_map: dict[str, Literal["income", "expense"]] = Field(
        default_factory=dict, max_length=40
    )

    @model_validator(mode="after")
    def distinct_columns(self):
        columns = [
            value
            for key, value in self.model_dump().items()
            if key.endswith("_column") and value is not None
        ]
        if len(columns) != len(set(columns)):
            raise ValueError("每个来源列只能映射一个字段")
        if any(
            len(key) > 1000 or len(value) > 36
            for key, value in self.category_map.items()
        ):
            raise ValueError("分类映射格式无效")
        if any(len(key) > 1000 for key in self.type_map):
            raise ValueError("类型映射名称过长")
        return self


class PreviewRequest(BaseModel):
    version: int = Field(ge=1)
    mapping: ImportMapping


class ConfirmRequest(BaseModel):
    version: int = Field(ge=1)
    row_ids: list[str] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_rows(self):
        if len(self.row_ids) != len(set(self.row_ids)) or any(
            len(value) > 36 for value in self.row_ids
        ):
            raise ValueError("行编号无效或重复")
        self.row_ids.sort()
        return self


class ImportRowPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    row_number: int
    raw: list[str] | None
    normalized: dict | None
    status: str
    messages: list[str]
    transaction_id: str | None


class ImportSummary(BaseModel):
    id: str
    filename: str
    status: str
    version: int
    created_at: datetime
    expires_at: datetime
    total: int
    counts: dict[str, int]


class ImportPublic(ImportSummary):
    format: str
    encoding: str
    sheet_name: str
    headers: list[str]
    mapping: ImportMapping | None
    rows: list[ImportRowPublic]
