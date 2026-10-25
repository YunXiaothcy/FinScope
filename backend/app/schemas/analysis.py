from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=2, max_length=500)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def paired_dates(self):
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("dates must be paired")
        return self


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]
    consent_target: str = Field(min_length=1, max_length=500)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Selection(BaseModel):
    # 模型只能选择已有证据和建议，不能返回自由文字、金额或查询语句。
    model_config = ConfigDict(extra="forbid", strict=True)
    fact_ids: list[str] = Field(min_length=1, max_length=8)
    advice_codes: list[str] = Field(max_length=3)


class Fact(BaseModel):
    id: str
    code: str
    group: str = ""
    label: str
    value: str
    unit: str
    start_date: date
    end_date: date
    formula: str


class AnalysisPublic(BaseModel):
    id: str
    status: str
    intent: str
    title: str
    provider: str
    target: str
    created_at: datetime
    expires_at: datetime
    facts: list[Fact]
    observations: list[str]
    advice: list[str]
    notices: list[str]
    tools: list[dict]
    cloud_payload: dict
    snapshot_hash: str
