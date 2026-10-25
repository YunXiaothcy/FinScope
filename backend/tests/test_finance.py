from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ledger(client: TestClient):
    auth = client.post(
        "/api/v1/auth/register",
        json={
            "email": "ledger@example.com",
            "password": "StrongPassword123!",
            "display_name": "虚构用户",
        },
    )
    assert auth.status_code == 201
    headers = {"X-CSRF-Token": auth.json()["csrf_token"]}
    space = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "测试账本", "type": "personal"},
    ).json()
    base = f"/api/v1/workspaces/{space['id']}/finance"
    accounts = []
    for name, opening in [("银行", "100.00"), ("现金", "0.00")]:
        response = client.post(
            f"{base}/accounts",
            headers=headers,
            json={"name": name, "opening_balance": opening},
        )
        assert response.status_code == 201, response.text
        accounts.append(response.json())
    categories = {}
    for name, kind in [("工资", "income"), ("餐饮", "expense")]:
        response = client.post(
            f"{base}/categories", headers=headers, json={"name": name, "type": kind}
        )
        assert response.status_code == 201, response.text
        categories[kind] = response.json()["id"]
    return {
        "client": client,
        "headers": headers,
        "space": space,
        "base": base,
        "accounts": accounts,
        "categories": categories,
    }


def post(ledger, kind="expense", amount="10.00", key=None, **overrides):
    payload = {
        "account_id": ledger["accounts"][0]["id"],
        "category_id": ledger["categories"].get(kind),
        "type": kind,
        "amount": amount,
        "occurred_at": datetime.now(UTC).isoformat(),
        **overrides,
    }
    headers = {**ledger["headers"], "Idempotency-Key": key or str(uuid4())}
    return ledger["client"].post(
        f"{ledger['base']}/transactions", json=payload, headers=headers
    )


def get_summary(ledger):
    response = ledger["client"].get(f"{ledger['base']}/summary")
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_decimal_and_large_values(ledger):
    assert post(ledger, "income", "0.10").status_code == 201
    assert post(ledger, "income", "0.20").status_code == 201
    assert get_summary(ledger)["income"] == "0.30"
    assert get_summary(ledger)["total_balance"] == "100.30"
    assert post(ledger, "income", "9999999999999999.99").status_code == 201
    assert get_summary(ledger)["income"] == "10000000000000000.29"


def test_transfer_is_not_income_and_replay_is_atomic(ledger):
    assert post(ledger, "income", "100.00").status_code == 201
    assert post(ledger, "expense", "10.25").status_code == 201
    payload = {
        "from_account_id": ledger["accounts"][0]["id"],
        "to_account_id": ledger["accounts"][1]["id"],
        "amount": "20.00",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    headers = {**ledger["headers"], "Idempotency-Key": str(uuid4())}
    first = ledger["client"].post(
        f"{ledger['base']}/transfers", headers=headers, json=payload
    )
    assert first.status_code == 201, first.text
    replay = ledger["client"].post(
        f"{ledger['base']}/transfers", headers=headers, json=payload
    )
    assert replay.json() == first.json()
    accounts = ledger["client"].get(f"{ledger['base']}/accounts").json()
    assert [a["balance"] for a in accounts] == ["169.75", "20.00"]
    summary = get_summary(ledger)
    assert (
        summary["income"],
        summary["expense"],
        summary["net"],
        summary["total_balance"],
    ) == ("100.00", "10.25", "89.75", "189.75")
    transfer = first.json()
    void = ledger["client"].post(
        f"{ledger['base']}/transfers/{transfer['id']}/void",
        headers=ledger["headers"],
        json={"version": transfer["version"]},
    )
    assert void.status_code == 200, void.text
    assert [
        a["balance"] for a in ledger["client"].get(f"{ledger['base']}/accounts").json()
    ] == ["189.75", "0.00"]
    entries = (
        ledger["client"]
        .get(f"{ledger['base']}/accounts/{ledger['accounts'][1]['id']}/movements")
        .json()
    )
    assert len(entries) == 2
    assert sum((Decimal(e["amount"]) for e in entries), Decimal(0)) == 0


def test_refunds_adjustments_and_limits(ledger):
    expense = post(ledger, "expense", "100.00").json()
    income = post(ledger, "income", "200.00").json()
    refunded = post(ledger, "refund", "30.00", related_transaction_id=expense["id"])
    assert refunded.status_code == 201, refunded.text
    assert (
        post(
            ledger, "refund", "70.01", related_transaction_id=expense["id"]
        ).status_code
        == 422
    )
    assert (
        post(ledger, "refund", "20.00", related_transaction_id=income["id"]).status_code
        == 201
    )
    assert (
        post(ledger, "adjustment", "5.00", adjustment_direction="increase").status_code
        == 201
    )
    summary = get_summary(ledger)
    assert (
        summary["income"],
        summary["expense"],
        summary["net"],
        summary["adjustment_net"],
        summary["cash_flow"],
        summary["total_balance"],
    ) == ("180.00", "70.00", "110.00", "5.00", "115.00", "215.00")
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/transactions/{expense['id']}/void",
            headers=ledger["headers"],
            json={"version": expense["version"]},
        )
        .status_code
        == 422
    )
    refund = refunded.json()
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/transactions/{refund['id']}/void",
            headers=ledger["headers"],
            json={"version": refund["version"]},
        )
        .status_code
        == 200
    )
    assert get_summary(ledger)["expense"] == "100.00"


def test_edit_void_and_audit_keep_history(ledger):
    record = post(ledger, amount="10.00").json()
    edit = {
        name: record[name]
        for name in (
            "account_id",
            "category_id",
            "type",
            "amount",
            "occurred_at",
            "description",
            "merchant_name",
            "related_transaction_id",
            "adjustment_direction",
            "version",
        )
    }
    edit["amount"] = "12.00"
    response = ledger["client"].put(
        f"{ledger['base']}/transactions/{record['id']}",
        headers=ledger["headers"],
        json=edit,
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert get_summary(ledger)["total_balance"] == "88.00"
    assert (
        ledger["client"]
        .put(
            f"{ledger['base']}/transactions/{record['id']}",
            headers=ledger["headers"],
            json=edit,
        )
        .status_code
        == 409
    )
    voided = ledger["client"].post(
        f"{ledger['base']}/transactions/{record['id']}/void",
        headers=ledger["headers"],
        json={"version": 2},
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["version"] == 3
    assert get_summary(ledger)["total_balance"] == "100.00"
    assert get_summary(ledger)["expense"] == "0.00"
    entries = (
        ledger["client"]
        .get(f"{ledger['base']}/accounts/{record['account_id']}/movements")
        .json()
    )
    assert len(entries) == 4
    assert sum((Decimal(e["amount"]) for e in entries), Decimal(0)) == 0
    logs = ledger["client"].get(f"{ledger['base']}/audit-logs").json()
    edits = [item for item in logs if item["action"] == "edit"]
    assert edits[0]["before"]["amount"] == "10.00"
    assert edits[0]["after"]["amount"] == "12.00"


@pytest.mark.parametrize(
    "amount", [0.1, "0.001", "NaN", "Infinity", "-1.00", "0.00", "10000000000000000.00"]
)
def test_invalid_money_never_posts(ledger, amount):
    assert post(ledger, amount=amount).status_code == 422
    assert get_summary(ledger)["total_balance"] == "100.00"


def test_idempotency_conflicts_and_required_header(ledger):
    timestamp = datetime.now(UTC).isoformat()
    key = str(uuid4())
    first = post(ledger, key=key, occurred_at=timestamp)
    replay = post(ledger, key=key, occurred_at=timestamp)
    assert first.status_code == 201, first.text
    assert replay.json() == first.json()
    assert (
        post(ledger, amount="11.00", key=key, occurred_at=timestamp).status_code == 409
    )
    assert ledger["client"].get(f"{ledger['base']}/transactions").json()["total"] == 1
    assert get_summary(ledger)["expense"] == "10.00"
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/transactions",
            headers=ledger["headers"],
            json={
                "account_id": ledger["accounts"][0]["id"],
                "category_id": ledger["categories"]["income"],
                "type": "income",
                "amount": "10.00",
                "occurred_at": timestamp,
            },
        )
        .status_code
        == 422
    )


def test_classification_and_account_category_isolation(ledger):
    wrong_category = post(
        ledger, "income", "20.00", category_id=ledger["categories"]["expense"]
    )
    assert wrong_category.status_code == 422
    other_space = (
        ledger["client"]
        .post(
            "/api/v1/workspaces",
            headers=ledger["headers"],
            json={"name": "另一空间", "type": "personal"},
        )
        .json()
    )
    other_base = f"/api/v1/workspaces/{other_space['id']}/finance"
    other_account = (
        ledger["client"]
        .post(
            f"{other_base}/accounts",
            headers=ledger["headers"],
            json={"name": "另一个账户"},
        )
        .json()
    )
    other_category = (
        ledger["client"]
        .post(
            f"{other_base}/categories",
            headers=ledger["headers"],
            json={"name": "支出", "type": "expense"},
        )
        .json()
    )
    assert post(ledger, account_id=other_account["id"]).status_code == 404
    assert post(ledger, category_id=other_category["id"]).status_code == 404
    assert get_summary(ledger)["total_balance"] == "100.00"


def test_viewer_read_only_and_outsider_denied(ledger):
    owner_cookie = ledger["client"].cookies.get("finscope_session")
    ledger["client"].cookies.clear()
    other = ledger["client"].post(
        "/api/v1/auth/register",
        json={
            "email": "viewer@example.com",
            "password": "StrongPassword123!",
            "display_name": "只读成员",
        },
    )
    viewer_cookie = ledger["client"].cookies.get("finscope_session")
    viewer_headers = {"X-CSRF-Token": other.json()["csrf_token"]}
    assert ledger["client"].get(f"{ledger['base']}/accounts").status_code == 404
    assert ledger["client"].get(f"{ledger['base']}/summary").status_code == 404
    ledger["client"].cookies.set("finscope_session", owner_cookie)
    assert (
        ledger["client"]
        .post(
            f"/api/v1/workspaces/{ledger['space']['id']}/members",
            headers=ledger["headers"],
            json={"email": "viewer@example.com", "role": "viewer"},
        )
        .status_code
        == 201
    )
    ledger["client"].cookies.set("finscope_session", viewer_cookie)
    assert ledger["client"].get(f"{ledger['base']}/accounts").status_code == 200
    assert (
        ledger["client"]
        .post(
            f"{ledger['base']}/accounts",
            headers=viewer_headers,
            json={"name": "不应创建"},
        )
        .status_code
        == 403
    )


def test_timezones_and_filters(ledger):
    # 香港 10 月 1 日 00:00 之前和之后各一笔，确保按工作空间口径过滤。
    assert (
        post(ledger, "income", "9.00", occurred_at="2026-09-30T15:59:59Z").status_code
        == 201
    )
    assert (
        post(
            ledger,
            "expense",
            "12.00",
            occurred_at="2026-09-30T16:00:00Z",
            description="虚构早餐",
        ).status_code
        == 201
    )
    summary = (
        ledger["client"]
        .get(f"{ledger['base']}/summary?start_date=2026-10-01&end_date=2026-10-01")
        .json()
    )
    assert summary["income"] == "0.00" and summary["expense"] == "12.00"
    filtered = (
        ledger["client"]
        .get(
            f"{ledger['base']}/transactions?start_date=2026-10-01&end_date=2026-10-01&min_amount=10.00&max_amount=15.00&q=早餐"
        )
        .json()
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["occurred_at"].endswith("Z")
    assert post(ledger, occurred_at="2026-10-01T12:00:00").status_code == 422
    assert (
        ledger["client"]
        .get(f"{ledger['base']}/summary?start_date=2026-10-02&end_date=2026-10-01")
        .status_code
        == 422
    )


def test_archived_accounts_and_same_account_transfer_are_rejected(ledger):
    account = ledger["accounts"][0]
    payload = {
        "from_account_id": account["id"],
        "to_account_id": account["id"],
        "amount": "10.00",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    response = ledger["client"].post(
        f"{ledger['base']}/transfers",
        headers={**ledger["headers"], "Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert response.status_code == 422
    archived = ledger["client"].patch(
        f"{ledger['base']}/accounts/{account['id']}",
        headers=ledger["headers"],
        json={"name": "已归档账户", "is_active": False, "opening_balance": "9999.00"},
    )
    assert archived.status_code == 200
    assert archived.json()["opening_balance"] == "100.00"
    assert post(ledger).status_code == 422
    assert get_summary(ledger)["total_balance"] == "100.00"


def test_creation_retry_after_void_does_not_resurrect_record(ledger):
    key, timestamp = str(uuid4()), datetime.now(UTC).isoformat()
    created = post(ledger, key=key, occurred_at=timestamp).json()
    voided = ledger["client"].post(
        f"{ledger['base']}/transactions/{created['id']}/void",
        headers=ledger["headers"],
        json={"version": created["version"]},
    )
    assert voided.status_code == 200
    replay = post(ledger, key=key, occurred_at=timestamp)
    assert replay.status_code == 201
    assert replay.json() == created
    records = ledger["client"].get(f"{ledger['base']}/transactions").json()
    assert records["total"] == 1
    assert records["items"][0]["status"] == "voided"
    assert get_summary(ledger)["total_balance"] == "100.00"
