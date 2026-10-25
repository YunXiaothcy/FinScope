import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_receipts import recognize, upload

from app.api.v1 import auth as auth_api
from app.core.config import security_configuration
from app.db.session import get_db
from app.main import app
from app.models import Attachment, AuthSession, User, WorkspaceMember
from app.ocr.extractors import ai_configuration
from app.ocr.providers import OcrResult
from app.security import auth, request_limits
from app.security.limits import WindowLimiter
from app.security.outbound import PublicHttpsTransport, allowed_endpoint

pytest_plugins = ["test_finance"]


def test_body_limits_reject_before_authentication_or_json_parsing(client):
    assert client.post("/api/v1/auth/login", content=b"x" * 8193).status_code == 413
    assert client.post("/api/v1/workspaces", content=b"x" * 131073).status_code == 413
    response = client.post(
        "/api/v1/auth/login", content=b"not-gzip", headers={"Content-Encoding": "gzip"}
    )
    assert response.status_code == 415
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


def raw_request(headers, chunks):
    async def run():
        output = []
        messages = iter(chunks)

        async def receive():
            return next(messages)

        async def send(message):
            output.append(message)

        async def forbidden(*args):
            pytest.fail("invalid input reached the application")

        await request_limits.RequestLimitsMiddleware(forbidden)(
            {"type": "http", "path": "/api/v1/auth/login", "headers": headers},
            receive,
            send,
        )
        return output[0]["status"]

    return asyncio.run(run())


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"1"), (b"content-length", b"1")],
        [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"xyz")],
    ],
)
def test_ambiguous_request_framing_is_rejected(headers):
    assert raw_request(headers, []) == 400


def test_chunked_overflow_and_declared_length_mismatch():
    assert (
        raw_request(
            [],
            [
                {"type": "http.request", "body": b"x" * 8192, "more_body": True},
                {"type": "http.request", "body": b"x"},
            ],
        )
        == 413
    )
    assert (
        raw_request(
            [(b"content-length", b"2")], [{"type": "http.request", "body": b"x"}]
        )
        == 400
    )


def test_slow_body_timeout_releases_capacity(monkeypatch):
    monkeypatch.setattr(request_limits, "BODY_TIMEOUT_SECONDS", 0.01)

    async def run():
        output = []

        async def slow():
            await asyncio.sleep(1)

        async def send(message):
            output.append(message)

        async def forbidden(*args):
            pytest.fail("slow body reached application")

        await request_limits.RequestLimitsMiddleware(forbidden)(
            {"type": "http", "path": "/api/v1/auth/login", "headers": []}, slow, send
        )
        return output[0]["status"]

    assert asyncio.run(run()) == 408
    assert request_limits._body_slots.acquire(blocking=False)
    request_limits._body_slots.release()


def test_rate_limit_is_atomic_and_has_a_bounded_key_count(monkeypatch):
    limiter = WindowLimiter(5, 60, max_keys=2)

    def consume(_):
        try:
            limiter.consume("shared")
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        assert sum(executor.map(consume, range(50))) == 5
    limiter.consume("second")
    with pytest.raises(HTTPException):
        limiter.consume("third")
    from app.security import limits

    monkeypatch.setattr(limits, "monotonic", lambda: 10**12)
    limiter.consume("third")
    assert list(limiter.entries) == ["third"]


def test_rotating_login_names_cannot_bypass_ip_budget(client, monkeypatch):
    monkeypatch.setattr(auth_api, "verify_password", lambda *args: False)
    for index in range(31):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": f"unknown-{index}@example.com",
                "password": "fictional-password",
            },
        )
        assert response.status_code == (401 if index < 30 else 429)


def test_logout_removes_exact_secure_host_cookie(client, monkeypatch):
    for module in (auth, auth_api):
        monkeypatch.setattr(module, "COOKIE_NAME", "__Host-finscope_session")
        monkeypatch.setattr(module, "cookie_secure", True)
    client.base_url = httpx.URL("https://testserver")
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "email": "secure-cookie@example.com",
            "password": "FictionalPassword123!",
            "display_name": "虚构用户",
        },
    )
    assert registration.status_code == 201
    response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": registration.json()["csrf_token"]},
    )
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-finscope_session=")
    assert all(
        value in cookie for value in ["Max-Age=0", "Secure", "HttpOnly", "Path=/"]
    )
    assert client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.parametrize(
    "mode,status",
    [
        ("revoked", 401),
        ("expired", 401),
        ("inactive", 401),
        ("viewer", 403),
        ("removed", 404),
    ],
)
def test_ocr_cannot_commit_after_authorization_changes(ledger, mode, status):
    receipt = upload(ledger).json()

    class RevokingProvider:
        def recognize(self, image):
            generator = app.dependency_overrides[get_db]()
            db = next(generator)
            try:
                session = db.scalar(select(AuthSession))
                if mode == "revoked":
                    session.revoked_at = datetime.now(UTC)
                elif mode == "expired":
                    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                elif mode == "inactive":
                    db.get(User, session.user_id).is_active = False
                else:
                    member = db.scalar(select(WorkspaceMember))
                    if mode == "viewer":
                        member.role = "viewer"
                    else:
                        db.delete(member)
                db.commit()
            finally:
                generator.close()
            return OcrResult("合计：88.50 CNY", "mock")

    assert recognize(ledger, receipt, RevokingProvider()).status_code == status
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        stored = db.get(Attachment, receipt["id"])
        assert stored.version == receipt["version"] and not stored.ocr_text
    finally:
        generator.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://example.com:8443/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?target=x",
        "https://example.com/../internal",
        "https://other.example/v1",
        "https://example.com/%2e%2e",
    ],
)
def test_outbound_destination_validation(url, monkeypatch):
    monkeypatch.setenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "example.com")
    assert not allowed_endpoint(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
def test_outbound_rejects_non_public_and_mixed_dns_answers(address, monkeypatch):
    monkeypatch.setenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "example.com")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
        ],
    )

    def forbidden(*args):
        pytest.fail("unsafe DNS result reached network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    with (
        PublicHttpsTransport("https://example.com/v1") as transport,
        pytest.raises(httpx.ConnectError),
    ):
        transport.handle_request(
            httpx.Request("POST", "https://example.com/v1/chat/completions")
        )


def test_outbound_pins_ip_but_preserves_tls_hostname_and_http_host(monkeypatch):
    monkeypatch.setenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "example.com")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
        ],
    )
    calls = []

    def capture(self, request):
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", capture)
    with PublicHttpsTransport("https://example.com/v1") as transport:
        transport.handle_request(
            httpx.Request(
                "POST", "https://example.com/v1/chat/completions", content=b"{}"
            )
        )
    assert calls[0].url.host == "93.184.215.14"
    assert calls[0].headers["Host"] == "example.com"
    assert calls[0].extensions["sni_hostname"] == "example.com"


def test_external_receipt_api_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("FINSCOPE_RECEIPT_AI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("FINSCOPE_RECEIPT_AI_MODEL", "test-only")
    monkeypatch.setenv("FINSCOPE_RECEIPT_AI_API_KEY", "fictional-test-value")
    monkeypatch.setenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "example.com")
    monkeypatch.delenv("FINSCOPE_RECEIPT_AI_ENABLED", raising=False)
    assert not ai_configuration()["enabled"]


def test_unknown_runtime_mode_fails_closed():
    with pytest.raises(RuntimeError):
        security_configuration({"FINSCOPE_ENV": "prodution"})
