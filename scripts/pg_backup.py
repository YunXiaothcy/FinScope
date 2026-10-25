"""PostgreSQL 备份、校验与恢复到全新数据库；标准库实现，可在维护容器运行。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def pg_environment(admin=False, database=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    user = (
        os.environ.get("PGADMIN_USER", "finscope_admin")
        if admin
        else os.environ.get("PGUSER", "finscope_app")
    )
    password_file = (
        os.environ.get("PGADMIN_PASSWORD_FILE")
        if admin
        else os.environ.get("PGPASSWORD_FILE")
    )
    if not password_file:
        raise ValueError("password_file_required")
    password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise ValueError("empty_password")
    env.update(
        PGHOST=os.environ.get("PGHOST", "db"),
        PGPORT=os.environ.get("PGPORT", "5432"),
        PGDATABASE=database or os.environ.get("PGDATABASE", "finscope"),
        PGUSER=user,
        PGPASSWORD=password,
        PGCONNECT_TIMEOUT="5",
        PGSSLMODE=os.environ.get("PGSSLMODE", "prefer"),
        PGTZ="UTC",
    )
    return env


def command(tool, arguments, env, stdout=None):
    binary = (
        str(Path(os.environ["FINSCOPE_PG_BIN"]) / tool)
        if os.environ.get("FINSCOPE_PG_BIN")
        else tool
    )
    result = subprocess.run(
        [binary, *arguments],
        env=env,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError("database_tool_failed")
    return result.stdout


def checksum(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(directory: Path):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    name = "finscope-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".dump"
    target = directory / name
    temporary = target.with_suffix(".partial")
    env = pg_environment()
    revision = (
        command(
            "psql",
            [
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT version_num FROM alembic_version",
            ],
            env,
        )
        .decode()
        .strip()
    )
    try:
        with temporary.open("xb") as stream:
            temporary.chmod(0o600)
            command(
                "pg_dump",
                ["--format=custom", "--no-owner", "--no-acl"],
                env,
                stdout=stream,
            )
        command("pg_restore", ["--list", str(temporary)], env)
        os.link(temporary, target)  # 目标已存在时失败，绝不覆盖已有备份。
        temporary.unlink()
        metadata = {
            "format": 1,
            "file": name,
            "sha256": checksum(target),
            "bytes": target.stat().st_size,
            "source_database": env["PGDATABASE"],
            "source_revision": revision,
            "created_at": datetime.now(UTC).isoformat(),
        }
        metadata_path = target.with_suffix(".json")
        with metadata_path.open("x", encoding="utf-8") as stream:
            json.dump(metadata, stream, indent=2)
        metadata_path.chmod(0o600)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


def verify(path: Path):
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if (
        metadata.get("format") != 1
        or metadata.get("file") != path.name
        or metadata.get("bytes") != path.stat().st_size
        or metadata.get("sha256") != checksum(path)
    ):
        raise ValueError("backup_integrity_failed")
    command(
        "pg_restore",
        ["--list", str(path)],
        {key: value for key, value in os.environ.items() if not key.startswith("PG")},
    )
    return metadata


def restore(path: Path, target: str):
    metadata = verify(path)
    if not re.fullmatch(
        r"finscope_restore_[a-z0-9_]{1,40}", target
    ) or target == os.environ.get("PGDATABASE", "finscope"):
        raise ValueError("restore_requires_a_new_named_database")
    admin = pg_environment(admin=True)
    app = pg_environment(database=target)
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", app["PGUSER"]):
        raise ValueError("invalid_application_role")
    existing = command(
        "psql",
        [
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"SELECT 1 FROM pg_database WHERE datname='{target}'",
        ],
        admin,
    ).strip()
    if existing:
        raise ValueError("restore_target_already_exists")
    # 不 DROP、不覆盖生产数据库；失败时保留新库供检查，下一次使用另一个新名称。
    command(
        "psql",
        [
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'CREATE DATABASE "{target}" OWNER "{app["PGUSER"]}"',
        ],
        admin,
    )
    command(
        "psql",
        [
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'REVOKE ALL ON DATABASE "{target}" FROM PUBLIC',
        ],
        admin,
    )
    command(
        "pg_restore",
        [
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
            target,
            str(path),
        ],
        app,
    )
    revision = (
        command(
            "psql",
            [
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT version_num FROM alembic_version",
            ],
            app,
        )
        .decode()
        .strip()
    )
    if revision != metadata["source_revision"]:
        raise ValueError("restored_migration_revision_mismatch")
    # 旧备份不能让已经退出/撤销的会话重新生效，恢复后要求所有用户重新登录。
    command(
        "psql",
        [
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL",
        ],
        app,
    )
    return target


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("backup").add_argument("--directory", type=Path, required=True)
    commands.add_parser("verify").add_argument("--file", type=Path, required=True)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--file", type=Path, required=True)
    restore_parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            result = backup(args.directory)
            print("Backup created and archive/checksum verified:", result.name)
        elif args.operation == "verify":
            verify(args.file)
            print("Archive and checksum verified")
        else:
            restore(args.file, args.target)
            print(
                "Restored into the new database and invalidated restored sessions; verify business data before switching"
            )
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit(
            "Backup/restore failed; source database was not overwritten. Check private files, connectivity, archive and target name"
        ) from None


if __name__ == "__main__":
    main()
