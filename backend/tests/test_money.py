from decimal import Decimal

from sqlalchemy import Column, MetaData, Table, create_engine, select, text

from app.db.money import Money


def test_sqlite_money_is_integer_and_round_trips_without_float():
    engine = create_engine("sqlite://")
    table = Table("exact_money", MetaData(), Column("amount", Money()))
    table.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(amount=Decimal("9999999999999999.99")))
        raw = connection.execute(
            text("SELECT typeof(amount), amount FROM exact_money")
        ).one()
        assert raw == ("integer", 999999999999999999)
        assert connection.scalar(select(table.c.amount)) == Decimal(
            "9999999999999999.99"
        )
    engine.dispose()
