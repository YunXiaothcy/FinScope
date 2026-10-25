"""Exact money on PostgreSQL and the local SQLite development database."""

import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import BigInteger, Numeric
from sqlalchemy.types import TypeDecorator

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
SUPPORTED_CURRENCIES = {"CNY", "HKD", "USD", "EUR"}


def validate_money(value: object) -> Decimal:
    # JSON 小数通常会先成为 float；金额接口因此要求字符串或整数。
    if isinstance(value, bool | float):
        raise ValueError("金额请使用十进制字符串")  # noqa: TRY004 Pydantic validators require ValueError.
    raw = str(value)
    if not re.fullmatch(r"-?\d{1,16}(?:\.\d{1,2})?", raw):
        raise ValueError("金额最多 16 位整数和 2 位小数")
    return Decimal(raw).quantize(CENT, rounding=ROUND_HALF_UP)


class Money(TypeDecorator):
    """SQLite 保存整数分，PostgreSQL 保存 NUMERIC(18,2)，均不经过 float。"""

    impl = Numeric(18, 2)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(
            BigInteger() if dialect.name == "sqlite" else Numeric(18, 2)
        )

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        amount = validate_money(value)
        return int(amount * 100) if dialect.name == "sqlite" else amount

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        amount = Decimal(value)
        return (amount / 100 if dialect.name == "sqlite" else amount).quantize(CENT)
