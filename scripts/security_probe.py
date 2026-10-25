"""Run bounded hostile-input checks against a new, disposable local API process.

Never targets the daily application, existing databases, or external services.
Run with the project's Python environment. Results are not a full penetration test.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]


def run():
    private = ROOT / ".local" / "security-probe"
    private.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="isolated-", dir=private) as temporary:
        directory = Path(temporary).resolve()
        if not directory.is_relative_to(private.resolve()):
            raise RuntimeError("Invalid test directory")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("FINSCOPE_")
        }
        env.update(
            {
                "FINSCOPE_ENV": "test",
                "FINSCOPE_OCR_PROVIDER": "disabled",
                "FINSCOPE_DATABASE_URL": URL.create(
                    "sqlite", database=str(directory / "fictional.db")
                ).render_as_string(),
            }
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT / "backend",
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
                "--no-server-header",
                "--no-proxy-headers",
            ],
            cwd=ROOT / "backend",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/v1/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError("Isolated API failed to start")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated API did not become ready")

                def check(name, response, status):
                    if response.status_code != status:
                        raise RuntimeError(
                            f"{name}: unexpected HTTP status {response.status_code}"
                        )
                    results.append(name)
                    return response.json() if response.content else None

                check(
                    "unauthenticated workspace access",
                    client.get("/api/v1/workspaces"),
                    401,
                )
                check(
                    "oversized authentication body",
                    client.post("/api/v1/auth/login", content=b"x" * 8193),
                    413,
                )
                check(
                    "compressed request denied",
                    client.post(
                        "/api/v1/auth/login",
                        content=b"x",
                        headers={"Content-Encoding": "gzip"},
                    ),
                    415,
                )
                check(
                    "SQL-like login input rejected",
                    client.post(
                        "/api/v1/auth/login",
                        json={"email": "' OR 1=1--", "password": "fictional"},
                    ),
                    422,
                )

                def register(email):
                    return check(
                        "fictional account registration",
                        client.post(
                            "/api/v1/auth/register",
                            json={
                                "email": email,
                                "display_name": "安全验证虚构用户",
                                "password": secrets.token_urlsafe(24) + "Aa1!",
                            },
                        ),
                        201,
                    )

                owner = register("probe-owner@example.com")
                headers = {"X-CSRF-Token": owner["csrf_token"]}
                cookie = client.cookies.get("finscope_session")
                check(
                    "missing CSRF rejected",
                    client.post(
                        "/api/v1/workspaces",
                        json={"name": "blocked", "type": "personal"},
                    ),
                    403,
                )
                check(
                    "foreign origin rejected",
                    client.post(
                        "/api/v1/workspaces",
                        headers={**headers, "Origin": "https://untrusted.example"},
                        json={"name": "blocked", "type": "personal"},
                    ),
                    403,
                )
                space = check(
                    "owner workspace creation",
                    client.post(
                        "/api/v1/workspaces",
                        headers=headers,
                        json={"name": "虚构隔离空间", "type": "personal"},
                    ),
                    201,
                )
                prefix = f"/api/v1/workspaces/{space['id']}"
                check(
                    "malicious filename rejected",
                    client.post(
                        prefix + "/imports",
                        headers={
                            **headers,
                            "Content-Type": "application/octet-stream",
                            "X-File-Name": "..%2Fsecret.csv",
                        },
                        content=b"x",
                    ),
                    422,
                )
                check(
                    "forged image rejected",
                    client.post(
                        prefix + "/receipts",
                        headers={
                            **headers,
                            "Content-Type": "application/octet-stream",
                            "X-File-Name": "receipt.png",
                        },
                        content=b"<script>alert(1)</script>",
                    ),
                    422,
                )
                response = client.get(prefix + "/analysis/capabilities")
                capability = check("financial AI reserved", response, 200)
                if (
                    capability["ai_enabled"]
                    or response.headers.get("Cache-Control") != "no-store"
                ):
                    raise RuntimeError("Privacy or reserved AI check failed")
                outsider = register("probe-outsider@example.com")
                for route in [
                    "/finance/accounts",
                    "/receipts",
                    "/imports",
                    "/analysis/capabilities",
                ]:
                    check(
                        "cross-workspace access denied " + route,
                        client.get(prefix + route),
                        404,
                    )
                check(
                    "cross-workspace write denied",
                    client.post(
                        prefix + "/finance/accounts",
                        headers={"X-CSRF-Token": outsider["csrf_token"]},
                        json={"name": "blocked", "opening_balance": "0.00"},
                    ),
                    404,
                )
                check(
                    "logout",
                    client.post(
                        "/api/v1/auth/logout",
                        headers={"X-CSRF-Token": outsider["csrf_token"]},
                    ),
                    204,
                )
                client.cookies.clear()
                client.cookies.set("finscope_session", cookie)
                check("owner remains authorized", client.get(prefix), 200)
                check(
                    "owner logout",
                    client.post("/api/v1/auth/logout", headers=headers),
                    204,
                )
                client.cookies.set("finscope_session", cookie)
                check(
                    "revoked session replay denied",
                    client.get("/api/v1/auth/session"),
                    401,
                )
                client.cookies.clear()
                for index in range(6):
                    response = client.post(
                        "/api/v1/auth/login",
                        headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
                        json={
                            "email": "probe-unknown@example.com",
                            "password": "fictional-password",
                        },
                    )
                    check(
                        f"login budget attempt {index + 1}",
                        response,
                        401 if index < 5 else 429,
                    )
            report = {
                "scope": "isolated localhost SQLite API; fictional inputs only",
                "passed": len(results),
                "checks": results,
            }
            (private / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"Isolated HTTP security checks passed: {len(results)}. Not a full penetration test."
            )
        finally:
            if os.name == "nt" and process.poll() is None:
                # Windows venv Python can have a launcher child; stop the owned tree.
                subprocess.run(
                    [
                        str(
                            Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
                        ),
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    run()
