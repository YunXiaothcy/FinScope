import logging

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import database_configuration, security_configuration
from app.db.session import get_db
from app.main import app, settings


def test_production_secret_file_url_preserves_reserved_password_characters(tmp_path):
    password = " test-only@password:with%reserved/characters "
    secret = tmp_path / "password"
    secret.write_text(password, encoding="utf-8")
    url = make_url(
        database_configuration(
            {
                "FINSCOPE_ENV": "production",
                "FINSCOPE_DATABASE_PASSWORD_FILE": str(secret),
                "FINSCOPE_DATABASE_HOST": "db",
                "FINSCOPE_DATABASE_USER": "finscope_app",
            }
        )
    )
    assert url.password == password and url.drivername == "postgresql+psycopg"
    assert password not in str(url)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///anything.db",
        "postgresql+psycopg://postgres:secret@db/finscope",
        "postgresql+psycopg://finscope_app@db/finscope",
    ],
)
def test_production_rejects_insecure_database_configuration(url):
    with pytest.raises(RuntimeError) as error:
        database_configuration(
            {"FINSCOPE_ENV": "production", "FINSCOPE_DATABASE_URL": url}
        )
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"FINSCOPE_COOKIE_SECURE": "false"},
        {"FINSCOPE_ALLOWED_ORIGINS": "http://finance.example.com"},
        {"FINSCOPE_ALLOWED_ORIGINS": "https://finance.example.com/path"},
        {"FINSCOPE_ALLOWED_ORIGINS": "https://finance.example.com?token=secret"},
        {"FINSCOPE_ALLOWED_HOSTS": "*"},
        {"FINSCOPE_ALLOWED_HOSTS": ""},
        {"FINSCOPE_ALLOWED_ORIGINS": ""},
        {"FINSCOPE_ALLOWED_ORIGINS": "https://evil.example.com"},
    ],
)
def test_production_requires_exact_https_origin_host_and_secure_cookie(change):
    env = {
        "FINSCOPE_ENV": "production",
        "FINSCOPE_COOKIE_SECURE": "true",
        "FINSCOPE_ALLOWED_HOSTS": "finance.example.com",
        "FINSCOPE_ALLOWED_ORIGINS": "https://finance.example.com",
        **change,
    }
    with pytest.raises(RuntimeError):
        security_configuration(env)


def test_readiness_checks_migration_not_only_a_live_python_process(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 503
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        db.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        db.execute(text("INSERT INTO alembic_version VALUES ('0006_ai_analysis')"))
        db.commit()
        assert client.get("/api/v1/ready").json() == {"status": "ready"}
        db.execute(text("UPDATE alembic_version SET version_num='old_version'"))
        db.commit()
        assert client.get("/api/v1/ready").status_code == 503
    finally:
        generator.close()


def test_production_error_logs_and_response_do_not_echo_private_exception(
    client, monkeypatch, caplog
):
    secret = "private-bank-reference-test-only"

    def broken_db():
        raise RuntimeError(secret)

    monkeypatch.setitem(settings, "production", True)
    app.dependency_overrides[get_db] = broken_db
    with caplog.at_level(logging.ERROR, logger="finscope"):
        response = client.get("https://testserver/api/v1/ready")
    assert response.status_code == 500
    assert secret not in response.text and secret not in caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_production_plain_http_cannot_accept_auth_payload(client, monkeypatch):
    monkeypatch.setitem(settings, "production", True)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "test-only-password"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "https_required"
