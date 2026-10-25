from fastapi.testclient import TestClient

from app.main import app


def test_independent_sessions_share_ledger_and_keep_logout_isolated(ledger):
    # 两个独立 Cookie 会话模拟设备；不是实际手机浏览器验收。
    with TestClient(app) as second:
        login = second.post(
            "/api/v1/auth/login",
            json={"email": "ledger@example.com", "password": "StrongPassword123!"},
        )
        assert login.status_code == 200
        headers = {
            "X-CSRF-Token": login.json()["csrf_token"],
            "Idempotency-Key": "second-device-entry",
        }
        payload = {
            "account_id": ledger["accounts"][0]["id"],
            "category_id": ledger["categories"]["expense"],
            "type": "expense",
            "amount": "12.34",
            "occurred_at": "2026-10-24T04:00:00Z",
        }
        response = second.post(
            f"{ledger['base']}/transactions", headers=headers, json=payload
        )
        assert response.status_code == 201
        assert (
            second.post(
                f"{ledger['base']}/transactions", headers=headers, json=payload
            ).json()["id"]
            == response.json()["id"]
        )
        for device in (ledger["client"], second):
            result = device.get(
                f"{ledger['base']}/summary?start_date=2026-10-01&end_date=2026-10-24"
            )
            assert result.status_code == 200
            assert result.json()["expense"] == "12.34"
            assert result.json()["total_balance"] == "87.66"
        assert second.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert second.get(f"{ledger['base']}/summary").status_code == 401
        assert ledger["client"].get(f"{ledger['base']}/summary").status_code == 200


pytest_plugins = ["test_finance"]
