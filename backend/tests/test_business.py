from decimal import Decimal
from uuid import uuid4

import pytest


@pytest.fixture
def business(client):
    auth = client.post(
        "/api/v1/auth/register",
        json={
            "email": "business@example.com",
            "password": "StrongPassword123!",
            "display_name": "虚构商户",
        },
    )
    headers = {"X-CSRF-Token": auth.json()["csrf_token"]}
    workspace = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "测试商户", "type": "business"},
    ).json()
    base = f"/api/v1/workspaces/{workspace['id']}"
    account = client.post(
        f"{base}/finance/accounts",
        headers=headers,
        json={"name": "商户账户", "opening_balance": "1000.00"},
    ).json()
    categories = {}
    for kind, name in [
        ("revenue", "销售"),
        ("cost", "采购成本"),
        ("expense", "租金费用"),
    ]:
        response = client.post(
            f"{base}/finance/categories",
            headers=headers,
            json={
                "name": name,
                "type": "income" if kind == "revenue" else "expense",
                "business_kind": kind,
            },
        )
        assert response.status_code == 201, response.text
        categories[kind] = response.json()["id"]
    return {
        "client": client,
        "headers": headers,
        "base": base,
        "account": account,
        "categories": categories,
    }


def keyed(business, key=None):
    return {**business["headers"], "Idempotency-Key": key or str(uuid4())}


def cash(business, kind, amount, **overrides):
    payload = {
        "account_id": business["account"]["id"],
        "category_id": business["categories"][kind],
        "type": "income" if kind == "revenue" else "expense",
        "amount": amount,
        "occurred_at": "2026-10-10T04:00:00Z",
        **overrides,
    }
    response = business["client"].post(
        f"{business['base']}/finance/transactions",
        headers=keyed(business),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def obligation(
    business, kind="receivable", amount="600.00", business_kind="revenue", **overrides
):
    payload = {
        "kind": kind,
        "category_id": business["categories"][business_kind],
        "amount": amount,
        "recognized_at": "2026-10-10T04:00:00Z",
        "due_date": "2026-10-20",
        "counterparty": "虚构客户或供应商",
        **overrides,
    }
    response = business["client"].post(
        f"{business['base']}/business/obligations",
        headers=keyed(business),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def settle(business, item, amount, key=None, **overrides):
    return business["client"].post(
        f"{business['base']}/business/obligations/{item['id']}/settlements",
        headers=keyed(business, key),
        json={
            "account_id": business["account"]["id"],
            "amount": amount,
            "occurred_at": "2026-10-12T04:00:00Z",
            "version": item["version"],
            **overrides,
        },
    )


def report(business, start="2026-10-01", end="2026-10-31"):
    response = business["client"].get(
        f"{business['base']}/business/report?start_date={start}&end_date={end}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_direct_profit_refunds_adjustments_and_cash_reconcile(business):
    cash(business, "revenue", "1000.00")
    cost = cash(business, "cost", "400.00")
    cash(business, "expense", "100.00")
    cash(
        business,
        "cost",
        "50.00",
        type="refund",
        category_id=None,
        related_transaction_id=cost["id"],
        occurred_at="2026-10-11T04:00:00Z",
    )
    cash(
        business,
        "cost",
        "20.00",
        type="adjustment",
        category_id=None,
        adjustment_direction="increase",
    )
    second = (
        business["client"]
        .post(
            f"{business['base']}/finance/accounts",
            headers=business["headers"],
            json={"name": "现金"},
        )
        .json()
    )
    transfer = business["client"].post(
        f"{business['base']}/finance/transfers",
        headers=keyed(business),
        json={
            "from_account_id": business["account"]["id"],
            "to_account_id": second["id"],
            "amount": "100.00",
            "occurred_at": "2026-10-12T04:00:00Z",
        },
    )
    assert transfer.status_code == 201
    result = report(business)
    assert (result["revenue"], result["cost"], result["expense"], result["profit"]) == (
        "1000.00",
        "350.00",
        "100.00",
        "550.00",
    )
    assert (
        result["cash_in"],
        result["cash_out"],
        result["operating_cash_flow"],
        result["net_cash_flow"],
    ) == ("1050.00", "500.00", "550.00", "570.00")
    assert (
        result["opening_balance"],
        result["closing_balance"],
        result["current_balance"],
    ) == ("1000.00", "1570.00", "1570.00")
    assert sum(
        (Decimal(row["profit"]) for row in result["daily"]), Decimal(0)
    ) == Decimal(result["profit"])


def test_obligations_recognize_profit_once_and_settle_partially(business):
    receivable = obligation(business)
    payable = obligation(business, "payable", "200.00", "cost")
    obligation(business, "payable", "50.00", "expense")
    before = report(business)
    assert (
        before["profit"],
        before["net_cash_flow"],
        before["receivable_balance"],
        before["payable_balance"],
    ) == ("350.00", "0.00", "600.00", "250.00")
    payment = settle(business, receivable, "400.00")
    assert payment.status_code == 201, payment.text
    assert payment.json()["source"] == "settlement"
    assert settle(business, payable, "100.00").status_code == 201
    after = report(business)
    assert (
        after["profit"],
        after["net_cash_flow"],
        after["receivable_balance"],
        after["payable_balance"],
        after["current_balance"],
    ) == ("350.00", "300.00", "200.00", "150.00", "1300.00")
    item = (
        business["client"]
        .get(f"{business['base']}/business/obligations/{receivable['id']}")
        .json()
    )
    assert item["settlement_status"] == "partial"
    assert item["paid_amount"] == "400.00" and item["remaining_amount"] == "200.00"
    assert after["overdue_receivable"] == "200.00"


def test_settlement_retry_stale_version_overpayment_and_context(business):
    item = obligation(business)
    key = str(uuid4())
    first = settle(business, item, "400.00", key)
    assert first.status_code == 201
    assert settle(business, item, "400.00", key).json() == first.json()
    assert settle(business, item, "100.00").status_code == 409
    updated = (
        business["client"]
        .get(f"{business['base']}/business/obligations/{item['id']}")
        .json()
    )
    assert settle(business, updated, "200.01").status_code == 422
    assert settle(business, updated, "200.00").status_code == 201
    assert (
        business["client"]
        .get(f"{business['base']}/business/obligations/{item['id']}")
        .json()["settlement_status"]
        == "settled"
    )
    other = obligation(business)
    assert settle(business, other, "400.00", key).status_code == 409
    assert report(business)["cash_in"] == "600.00"


def test_void_settlement_reopens_item_and_prevents_free_edit_or_refund(business):
    item = obligation(business)
    payment = settle(business, item, "100.00").json()
    edit = {
        name: payment[name]
        for name in (
            "account_id",
            "category_id",
            "type",
            "amount",
            "occurred_at",
            "version",
        )
    }
    edit["amount"] = "900.00"
    assert (
        business["client"]
        .put(
            f"{business['base']}/finance/transactions/{payment['id']}",
            headers=business["headers"],
            json=edit,
        )
        .status_code
        == 422
    )
    assert (
        business["client"]
        .post(
            f"{business['base']}/finance/transactions",
            headers=keyed(business),
            json={
                "account_id": payment["account_id"],
                "type": "refund",
                "amount": "10.00",
                "occurred_at": "2026-10-15T04:00:00Z",
                "related_transaction_id": payment["id"],
            },
        )
        .status_code
        == 422
    )
    current = (
        business["client"]
        .get(f"{business['base']}/business/obligations/{item['id']}")
        .json()
    )
    assert (
        business["client"]
        .post(
            f"{business['base']}/business/obligations/{item['id']}/void",
            headers=business["headers"],
            json={"version": current["version"]},
        )
        .status_code
        == 422
    )
    voided = business["client"].post(
        f"{business['base']}/finance/transactions/{payment['id']}/void",
        headers=business["headers"],
        json={"version": payment["version"]},
    )
    assert voided.status_code == 200, voided.text
    reopened = (
        business["client"]
        .get(f"{business['base']}/business/obligations/{item['id']}")
        .json()
    )
    assert (
        reopened["remaining_amount"] == "600.00"
        and reopened["settlement_status"] == "unsettled"
    )
    assert reopened["version"] > current["version"]
    assert (
        business["client"]
        .post(
            f"{business['base']}/business/obligations/{item['id']}/void",
            headers=business["headers"],
            json={"version": reopened["version"]},
        )
        .status_code
        == 200
    )
    assert (
        report(business)["profit"] == "0.00"
        and report(business)["current_balance"] == "1000.00"
    )


def test_recognition_and_payment_in_different_periods(business):
    item = obligation(
        business, recognized_at="2026-09-30T04:00:00Z", due_date="2026-10-01"
    )
    assert (
        settle(business, item, "250.00", occurred_at="2026-10-05T04:00:00Z").status_code
        == 201
    )
    september = report(business, "2026-09-01", "2026-09-30")
    october = report(business)
    assert (
        september["profit"],
        september["net_cash_flow"],
        september["receivable_balance"],
    ) == ("600.00", "0.00", "600.00")
    assert (
        october["profit"],
        october["net_cash_flow"],
        october["receivable_balance"],
    ) == ("0.00", "250.00", "350.00")
    assert (
        report(business, "2026-10-01", "2026-10-04")["receivable_balance"] == "600.00"
    )


def test_business_roles_and_personal_space_restrictions(business):
    client = business["client"]
    personal = client.post(
        "/api/v1/workspaces",
        headers=business["headers"],
        json={"name": "个人", "type": "personal"},
    ).json()
    assert (
        client.get(f"/api/v1/workspaces/{personal['id']}/business/report").status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/workspaces/{personal['id']}/finance/categories",
            headers=business["headers"],
            json={"name": "成本", "type": "expense", "business_kind": "cost"},
        ).status_code
        == 422
    )
    owner_cookie = client.cookies.get("finscope_session")
    client.cookies.clear()
    viewer = client.post(
        "/api/v1/auth/register",
        json={
            "email": "business-viewer@example.com",
            "password": "StrongPassword123!",
            "display_name": "只读成员",
        },
    )
    viewer_cookie = client.cookies.get("finscope_session")
    assert client.get(f"{business['base']}/business/report").status_code == 404
    client.cookies.set("finscope_session", owner_cookie)
    assert (
        client.post(
            f"{business['base']}/members",
            headers=business["headers"],
            json={"email": "business-viewer@example.com", "role": "viewer"},
        ).status_code
        == 201
    )
    client.cookies.set("finscope_session", viewer_cookie)
    assert client.get(f"{business['base']}/business/report").status_code == 200
    denied = client.post(
        f"{business['base']}/business/obligations",
        headers={
            "X-CSRF-Token": viewer.json()["csrf_token"],
            "Idempotency-Key": str(uuid4()),
        },
        json={
            "kind": "receivable",
            "category_id": business["categories"]["revenue"],
            "amount": "10.00",
            "recognized_at": "2026-10-10T04:00:00Z",
            "due_date": "2026-10-20",
            "counterparty": "虚构",
        },
    )
    assert denied.status_code == 403


def test_business_validates_links_classification_and_dates(business):
    item = obligation(business)
    assert (
        settle(business, item, "1.00", occurred_at="2026-10-01T04:00:00Z").status_code
        == 422
    )
    other = (
        business["client"]
        .post(
            "/api/v1/workspaces",
            headers=business["headers"],
            json={"name": "其他商户", "type": "business"},
        )
        .json()
    )
    account = (
        business["client"]
        .post(
            f"/api/v1/workspaces/{other['id']}/finance/accounts",
            headers=business["headers"],
            json={"name": "其他账户"},
        )
        .json()
    )
    assert settle(business, item, "1.00", account_id=account["id"]).status_code == 404
    malformed = business["client"].post(
        f"{business['base']}/business/obligations",
        headers=keyed(business),
        json={
            "kind": "payable",
            "category_id": business["categories"]["revenue"],
            "amount": "10.00",
            "recognized_at": "2026-10-10T04:00:00Z",
            "due_date": "2026-10-20",
            "counterparty": "虚构",
        },
    )
    assert malformed.status_code == 422
    changed = business["client"].patch(
        f"{business['base']}/finance/categories/{business['categories']['cost']}",
        headers=business["headers"],
        json={"name": "改名", "type": "expense", "business_kind": "expense"},
    )
    assert changed.status_code == 422


def test_obligation_retry_recognizes_profit_only_once(business):
    payload = {
        "kind": "receivable",
        "category_id": business["categories"]["revenue"],
        "amount": "600.00",
        "recognized_at": "2026-10-10T04:00:00Z",
        "due_date": "2026-10-20",
        "counterparty": "虚构客户",
    }
    key = str(uuid4())
    first = business["client"].post(
        f"{business['base']}/business/obligations",
        headers=keyed(business, key),
        json=payload,
    )
    assert first.status_code == 201
    retry = business["client"].post(
        f"{business['base']}/business/obligations",
        headers=keyed(business, key),
        json=payload,
    )
    assert retry.json() == first.json()
    changed = business["client"].post(
        f"{business['base']}/business/obligations",
        headers=keyed(business, key),
        json={**payload, "amount": "700.00"},
    )
    assert changed.status_code == 409
    assert (
        len(business["client"].get(f"{business['base']}/business/obligations").json())
        == 1
    )
    assert report(business)["profit"] == "600.00"


def test_obligation_category_and_item_cannot_cross_spaces(business):
    client = business["client"]
    item = obligation(business)
    other = client.post(
        "/api/v1/workspaces",
        headers=business["headers"],
        json={"name": "另一商户", "type": "business"},
    ).json()
    base = f"/api/v1/workspaces/{other['id']}"
    category = client.post(
        f"{base}/finance/categories",
        headers=business["headers"],
        json={"name": "其他收入", "type": "income"},
    ).json()
    forged = client.post(
        f"{business['base']}/business/obligations",
        headers=keyed(business),
        json={
            "kind": "receivable",
            "category_id": category["id"],
            "amount": "10.00",
            "recognized_at": "2026-10-10T04:00:00Z",
            "due_date": "2026-10-20",
            "counterparty": "虚构客户",
        },
    )
    assert forged.status_code == 404
    assert client.get(f"{base}/business/obligations/{item['id']}").status_code == 404
    assert (
        client.get(f"{base}/business/obligations/{item['id']}/settlements").status_code
        == 404
    )
