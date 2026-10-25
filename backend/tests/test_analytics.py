from decimal import Decimal

import pytest
from test_business import cash, obligation, settle
from test_finance import post

pytest_plugins = ["test_business", "test_finance"]


def read(data, start="2026-10-01", end="2026-10-03"):
    base = data["base"].removesuffix("/finance")
    return data["client"].get(
        f"{base}/analytics/dashboard?start_date={start}&end_date={end}"
    )


def test_empty_period_has_dense_days_and_carried_balance(ledger):
    response = read(ledger)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert not data["has_activity"]
    assert data["income"] == data["expense"] == data["net"] == "0.00"
    assert data["opening_balance"] == data["closing_balance"] == "100.00"
    assert [day["date"] for day in data["daily"]] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
    ]
    assert all(day["closing_balance"] == "100.00" for day in data["daily"])
    assert data["cost"] is None and data["receivable_balance"] is None


def test_personal_refund_boundary_adjustments_transfer_and_reconciliation(ledger):
    # 香港 10 月 1 日之前一秒，作为期初；当天第一秒的退款使该日净支出为负。
    expense = post(ledger, amount="20.00", occurred_at="2026-09-30T15:59:59Z").json()
    assert (
        post(
            ledger,
            "refund",
            "5.00",
            related_transaction_id=expense["id"],
            occurred_at="2026-09-30T16:00:00Z",
        ).status_code
        == 201
    )
    assert (
        post(ledger, "income", "0.10", occurred_at="2026-10-01T01:00:00Z").status_code
        == 201
    )
    assert (
        post(ledger, "income", "0.20", occurred_at="2026-10-01T01:00:00Z").status_code
        == 201
    )
    assert (
        post(
            ledger,
            "adjustment",
            "2.00",
            adjustment_direction="decrease",
            occurred_at="2026-10-02T01:00:00Z",
        ).status_code
        == 201
    )
    assert (
        post(ledger, "income", "7.00", occurred_at="2026-10-03T16:00:00Z").status_code
        == 201
    )
    transfer = ledger["client"].post(
        f"{ledger['base']}/transfers",
        headers={**ledger["headers"], "Idempotency-Key": "dashboard-transfer"},
        json={
            "from_account_id": ledger["accounts"][0]["id"],
            "to_account_id": ledger["accounts"][1]["id"],
            "amount": "9.00",
            "occurred_at": "2026-10-01T02:00:00Z",
        },
    )
    assert transfer.status_code == 201
    data = read(ledger).json()
    assert (data["income"], data["expense"], data["net"]) == ("0.30", "-5.00", "5.30")
    assert (data["cash_in"], data["cash_out"], data["adjustment_net"]) == (
        "5.30",
        "0.00",
        "-2.00",
    )
    assert (
        data["opening_balance"],
        data["closing_balance"],
        data["current_balance"],
    ) == ("80.00", "83.30", "90.30")
    assert data["daily"][0]["closing_balance"] == "85.30"
    assert data["daily"][1]["closing_balance"] == "83.30"
    assert data["daily"][-1]["closing_balance"] == data["closing_balance"]
    assert sum(
        (Decimal(row["net_cash_flow"]) for row in data["daily"]), Decimal(0)
    ) == Decimal(data["net_cash_flow"])
    assert sum(
        (Decimal(row["balance"]) for row in data["accounts"]), Decimal(0)
    ) == Decimal(data["current_balance"])
    single = read(ledger, "2026-10-02", "2026-10-02").json()
    assert single["net"] == "0.00" and single["net_cash_flow"] == "-2.00"
    assert single["opening_balance"] == "85.30"


def test_voided_records_and_large_amounts(ledger):
    old = post(ledger, "income", "5.00", occurred_at="2026-10-01T01:00:00Z").json()
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/transactions/{old['id']}/void",
            headers=ledger["headers"],
            json={"version": old["version"]},
        )
        .status_code
        == 200
    )
    assert (
        post(
            ledger, "income", "9999999999999999.99", occurred_at="2026-10-01T01:00:00Z"
        ).status_code
        == 201
    )
    data = read(ledger).json()
    assert data["income"] == "9999999999999999.99"
    assert data["daily"][0]["closing_balance"] == "10000000000000099.99"


def test_business_profit_and_cross_period_settlement_match_report(business):
    item = obligation(
        business, recognized_at="2026-09-15T01:00:00Z", due_date="2026-10-20"
    )
    assert (
        settle(business, item, "200.00", occurred_at="2026-10-02T01:00:00Z").status_code
        == 201
    )
    cash(business, "revenue", "100.00", occurred_at="2026-10-01T01:00:00Z")
    cash(business, "cost", "40.00", occurred_at="2026-10-01T01:00:00Z")
    cash(business, "expense", "10.00", occurred_at="2026-10-01T01:00:00Z")
    data = read(business).json()
    assert data["workspace_type"] == "business"
    assert (data["income"], data["expense"], data["net"]) == (
        "100.00",
        "50.00",
        "50.00",
    )
    assert (data["cost"], data["operating_expense"], data["receivable_balance"]) == (
        "40.00",
        "10.00",
        "400.00",
    )
    assert data["net_cash_flow"] == "250.00"
    assert data["daily"][1]["net"] == "0.00"
    original = (
        business["client"]
        .get(
            f"{business['base']}/business/report?start_date=2026-10-01&end_date=2026-10-03"
        )
        .json()
    )
    for key in ("closing_balance", "net_cash_flow", "income", "net"):
        assert (
            data[key] == original[{"income": "revenue", "net": "profit"}.get(key, key)]
        )
    previous = read(business, "2026-09-01", "2026-09-30").json()
    assert previous["income"] == previous["receivable_balance"] == "600.00"
    assert previous["net_cash_flow"] == "0.00"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-03", "2026-10-01"),
        ("2025-01-01", "2026-01-02"),
        ("2026-99-01", "2026-10-01"),
        ("9999-12-31", "9999-12-31"),
    ],
)
def test_invalid_or_excessive_periods(ledger, start, end):
    assert read(ledger, start, end).status_code == 422


def test_maximum_period_and_default_month(ledger):
    data = read(ledger, "2024-01-01", "2024-12-31").json()
    assert len(data["daily"]) == 366
    assert (
        ledger["client"]
        .get(f"{ledger['base'].removesuffix('/finance')}/analytics/dashboard")
        .status_code
        == 200
    )


def test_authorization_and_viewer_access(ledger):
    owner_cookie = ledger["client"].cookies.get("finscope_session")
    ledger["client"].cookies.clear()
    assert read(ledger).status_code == 401
    assert (
        ledger["client"]
        .post(
            "/api/v1/auth/register",
            json={
                "email": "dashboard-viewer@example.com",
                "password": "StrongPassword123!",
                "display_name": "只读成员",
            },
        )
        .status_code
        == 201
    )
    viewer_cookie = ledger["client"].cookies.get("finscope_session")
    assert read(ledger).status_code == 404
    ledger["client"].cookies.set("finscope_session", owner_cookie)
    assert (
        ledger["client"]
        .post(
            f"/api/v1/workspaces/{ledger['space']['id']}/members",
            headers=ledger["headers"],
            json={"email": "dashboard-viewer@example.com", "role": "viewer"},
        )
        .status_code
        == 201
    )
    ledger["client"].cookies.set("finscope_session", viewer_cookie)
    assert read(ledger).status_code == 200


def test_other_workspace_data_does_not_enter_statistics(ledger):
    space = (
        ledger["client"]
        .post(
            "/api/v1/workspaces",
            headers=ledger["headers"],
            json={"name": "隔离空间", "type": "personal"},
        )
        .json()
    )
    assert (
        ledger["client"]
        .post(
            f"/api/v1/workspaces/{space['id']}/finance/accounts",
            headers=ledger["headers"],
            json={"name": "隔离账户", "opening_balance": "999.00"},
        )
        .status_code
        == 201
    )
    data = read(ledger).json()
    assert data["current_balance"] == "100.00"
    assert len(data["accounts"]) == 2
