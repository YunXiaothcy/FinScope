from fastapi.testclient import TestClient


def register(client: TestClient, email: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPassword123!",
            "display_name": "测试用户",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_login_logout_and_cookie(client: TestClient) -> None:
    assert client.get("/api/v1/auth/session").status_code == 401
    auth = register(client, "Alice@Example.com")
    assert auth["user"]["email"] == "alice@example.com"
    cookie = client.cookies.get("finscope_session")
    assert cookie

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["email"] == "alice@example.com"
    assert (
        "httponly"
        in client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "StrongPassword123!"},
        )
        .headers["set-cookie"]
        .lower()
    )

    assert client.post("/api/v1/auth/logout").status_code == 403
    csrf = client.get("/api/v1/auth/session").json()["csrf_token"]
    assert (
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code
        == 204
    )
    assert client.get("/api/v1/auth/session").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "wrong"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "StrongPassword123!"},
        ).status_code
        == 200
    )


def test_validation_duplicate_and_origin(client: TestClient) -> None:
    invalid = client.post(
        "/api/v1/auth/register",
        json={"email": "bad", "password": "short", "display_name": "X"},
    )
    assert invalid.status_code == 422
    assert "short" not in invalid.text
    register(client, "same@example.com")
    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "email": "same@example.com",
            "password": "StrongPassword123!",
            "display_name": "X",
        },
    )
    assert duplicate.status_code == 409
    blocked = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.example"},
        json={"email": "same@example.com", "password": "StrongPassword123!"},
    )
    assert blocked.status_code == 403


def test_workspace_isolation_and_member_roles(client: TestClient) -> None:
    alice = register(client, "alice@example.com")
    alice_cookie = client.cookies.get("finscope_session")
    alice_csrf = alice["csrf_token"]
    created = client.post(
        "/api/v1/workspaces",
        headers={"X-CSRF-Token": alice_csrf},
        json={"name": "我的商户", "type": "business", "base_currency": "cny"},
    )
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["role"] == "owner"
    assert workspace["base_currency"] == "CNY"

    client.cookies.clear()
    bob = register(client, "bob@example.com")
    bob_cookie = client.cookies.get("finscope_session")
    assert client.get("/api/v1/workspaces").json() == []
    assert client.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 404
    assert (
        client.get(f"/api/v1/workspaces/{workspace['id']}/members").status_code == 404
    )

    client.cookies.set("finscope_session", alice_cookie)
    added = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        headers={"X-CSRF-Token": alice_csrf},
        json={"email": "bob@example.com", "role": "viewer"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["role"] == "viewer"

    client.cookies.set("finscope_session", bob_cookie)
    assert (
        client.get(f"/api/v1/workspaces/{workspace['id']}").json()["role"] == "viewer"
    )
    assert len(client.get(f"/api/v1/workspaces/{workspace['id']}/members").json()) == 2
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        headers={"X-CSRF-Token": bob["csrf_token"]},
        json={"email": "alice@example.com", "role": "admin"},
    )
    assert forbidden.status_code == 403


def test_workspace_create_requires_csrf(client: TestClient) -> None:
    register(client, "csrf@example.com")
    response = client.post(
        "/api/v1/workspaces", json={"name": "个人", "type": "personal"}
    )
    assert response.status_code == 403
    assert client.get("/api/v1/workspaces").json() == []


def test_profile_and_password_change(client: TestClient) -> None:
    auth = register(client, "profile@example.com")
    csrf = auth["csrf_token"]
    changed = client.patch(
        "/api/v1/auth/profile",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "  新名称  "},
    )
    assert changed.status_code == 200
    assert changed.json()["display_name"] == "新名称"
    assert client.get("/api/v1/auth/session").json()["user"]["display_name"] == "新名称"

    first_cookie = client.cookies.get("finscope_session")
    second_login = client.post(
        "/api/v1/auth/login",
        json={"email": "profile@example.com", "password": "StrongPassword123!"},
    )
    assert second_login.status_code == 200
    csrf = second_login.json()["csrf_token"]
    second_cookie = client.cookies.get("finscope_session")

    incorrect = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={"current_password": "wrong", "new_password": "AnotherStrong123!"},
    )
    assert incorrect.status_code == 400
    changed_password = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": "StrongPassword123!",
            "new_password": "AnotherStrong123!",
        },
    )
    assert changed_password.status_code == 204
    client.cookies.set("finscope_session", first_cookie)
    assert client.get("/api/v1/auth/session").status_code == 401
    client.cookies.set("finscope_session", second_cookie)
    assert client.get("/api/v1/auth/session").status_code == 200
    client.cookies.clear()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "profile@example.com", "password": "StrongPassword123!"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "profile@example.com", "password": "AnotherStrong123!"},
        ).status_code
        == 200
    )


def test_login_rate_limit(client: TestClient) -> None:
    register(client, "limited@example.com")
    client.cookies.clear()
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "incorrect"},
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login",
        json={"email": "limited@example.com", "password": "StrongPassword123!"},
    )
    assert blocked.status_code == 429
