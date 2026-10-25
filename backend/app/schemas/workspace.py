from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: Literal["personal", "business"]
    base_currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class WorkspacePublic(BaseModel):
    id: str
    name: str
    type: Literal["personal", "business"]
    base_currency: str
    role: Literal["owner", "admin", "member", "viewer"]
    created_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"]


class MemberPublic(BaseModel):
    user_id: str
    email: EmailStr
    display_name: str
    role: Literal["owner", "admin", "member", "viewer"]
