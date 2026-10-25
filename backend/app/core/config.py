import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DEV_ORIGINS = "http://127.0.0.1:5174,http://localhost:5174,http://127.0.0.1:8001,http://127.0.0.1:4174,http://localhost:4174"


def production_mode(env) -> bool:
    mode = env.get("FINSCOPE_ENV", "development")
    if mode not in {"development", "test", "production"}:
        raise RuntimeError("FINSCOPE_ENV must be development, test or production")
    return mode == "production"


def database_configuration(environ=None) -> str:
    env = os.environ if environ is None else environ
    value = env.get("FINSCOPE_DATABASE_URL")
    if not value and env.get("FINSCOPE_DATABASE_PASSWORD_FILE"):
        try:
            password = (
                Path(env["FINSCOPE_DATABASE_PASSWORD_FILE"])
                .read_text(encoding="utf-8")
                .rstrip("\r\n")
            )
            if not password:
                raise ValueError("empty_secret")
            value = URL.create(
                "postgresql+psycopg",
                username=env.get("FINSCOPE_DATABASE_USER", "finscope_app"),
                password=password,
                host=env.get("FINSCOPE_DATABASE_HOST", "db"),
                port=int(env.get("FINSCOPE_DATABASE_PORT", "5432")),
                database=env.get("FINSCOPE_DATABASE_NAME", "finscope"),
            ).render_as_string(hide_password=False)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Database secret/configuration is unavailable") from exc
    value = value or "sqlite:///./finscope_dev.db"
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        raise RuntimeError("Invalid database configuration") from None
    if production_mode(env):
        if (
            url.drivername != "postgresql+psycopg"
            or not url.host
            or not url.database
            or not url.password
        ):
            raise RuntimeError("Production requires authenticated PostgreSQL")
        if url.username in ("postgres", "finscope_admin"):
            raise RuntimeError(
                "Production API must use a dedicated non-admin database role"
            )
    return value


def security_configuration(environ=None) -> dict:
    env = os.environ if environ is None else environ
    production = production_mode(env)
    origins = {
        value.strip()
        for value in env.get(
            "FINSCOPE_ALLOWED_ORIGINS", "" if production else DEV_ORIGINS
        ).split(",")
        if value.strip()
    }
    hosts = [
        value.strip()
        for value in env.get("FINSCOPE_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    ]
    secure_value = env.get("FINSCOPE_COOKIE_SECURE", "false").lower()
    if secure_value not in {"true", "false"}:
        raise RuntimeError("FINSCOPE_COOKIE_SECURE must be true or false")
    secure = secure_value == "true"
    if production:
        if not secure:
            raise RuntimeError("Production requires Secure session cookies")
        if not hosts or any(
            not re.fullmatch(r"[A-Za-z0-9.-]+", value) or ".." in value
            for value in hosts
        ):
            raise RuntimeError("Production requires explicit trusted hosts")
        if not origins:
            raise RuntimeError("Production requires explicit HTTPS origins")
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                valid = (
                    parsed.scheme == "https"
                    and parsed.hostname in hosts
                    and not parsed.username
                    and not parsed.password
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.port != 0
                )
            except ValueError:
                valid = False
            if not valid:
                raise RuntimeError(
                    "Production origins must be exact HTTPS origins on trusted hosts"
                )
    return {
        "production": production,
        "origins": origins,
        "hosts": hosts,
        "secure": secure,
    }
