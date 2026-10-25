import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    specification = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_prepare_never_overwrites_private_passwords_and_rejects_injection(tmp_path):
    module = script("prepare_deploy")
    target = tmp_path / "private"
    module.prepare(target, "finance.example.com")
    password = (target / "secrets/app_password").read_bytes()
    with pytest.raises(FileExistsError):
        module.prepare(target, "another.example.com")
    assert (target / "secrets/app_password").read_bytes() == password
    assert password.strip() not in (target / ".env.production").read_bytes()
    with pytest.raises(ValueError):
        module.prepare(tmp_path / "invalid", "example.com;return 200")
    assert not (tmp_path / "invalid").exists()
    assert module.check(target, require_certificate=False)


def test_backup_tampering_is_rejected_before_any_database_command(
    tmp_path, monkeypatch
):
    module = script("pg_backup")
    archive = tmp_path / "fake.dump"
    archive.write_bytes(b"test-only-archive")
    archive.with_suffix(".json").write_text(
        json.dumps(
            {
                "format": 1,
                "file": archive.name,
                "bytes": archive.stat().st_size,
                "sha256": module.checksum(archive),
            }
        )
    )
    archive.write_bytes(b"tampered-financial-backup")

    def forbidden(*args, **kwargs):
        pytest.fail("tampered backup must not reach database tools")

    monkeypatch.setattr(module, "command", forbidden)
    with pytest.raises(ValueError, match="integrity"):
        module.verify(archive)


@pytest.mark.parametrize(
    "target",
    ["finscope", "postgres", "finscope_restore_bad';DROP DATABASE finscope;--"],
)
def test_restore_refuses_source_database_or_sql_injection(
    tmp_path, monkeypatch, target
):
    module = script("pg_backup")
    monkeypatch.setattr(module, "verify", lambda path: {})

    def forbidden(*args, **kwargs):
        pytest.fail("invalid target must not read admin credentials")

    monkeypatch.setattr(module, "pg_environment", forbidden)
    with pytest.raises(ValueError):
        module.restore(tmp_path / "fake.dump", target)


def test_restore_does_not_drop_or_overwrite_an_existing_database(tmp_path, monkeypatch):
    module = script("pg_backup")
    monkeypatch.setattr(module, "verify", lambda path: {})
    monkeypatch.setattr(
        module, "pg_environment", lambda **kwargs: {"PGUSER": "finscope_app"}
    )
    calls = []

    def existing_database(tool, args, env):
        calls.append((tool, args))
        return b"1\n"

    monkeypatch.setattr(module, "command", existing_database)
    with pytest.raises(ValueError, match="already_exists"):
        module.restore(tmp_path / "fake.dump", "finscope_restore_test")
    assert len(calls) == 1
    assert not any(
        "DROP" in argument or "CREATE DATABASE" in argument
        for _, args in calls
        for argument in args
    )


def test_restore_revokes_old_sessions_after_successful_restore(tmp_path, monkeypatch):
    module = script("pg_backup")
    monkeypatch.setattr(
        module, "verify", lambda path: {"source_revision": "0006_ai_analysis"}
    )
    monkeypatch.setattr(
        module, "pg_environment", lambda **kwargs: {"PGUSER": "finscope_app"}
    )
    calls = []

    def execute(tool, args, env):
        calls.append((tool, args))
        return (
            b"0006_ai_analysis\n"
            if "SELECT version_num FROM alembic_version" in args
            else b""
        )

    monkeypatch.setattr(module, "command", execute)
    assert (
        module.restore(tmp_path / "fake.dump", "finscope_restore_safe")
        == "finscope_restore_safe"
    )
    assert calls[-1][0] == "psql"
    assert (
        "UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL"
        in calls[-1][1]
    )
    assert (
        next(i for i, (tool, _) in enumerate(calls) if tool == "pg_restore")
        < len(calls) - 1
    )
