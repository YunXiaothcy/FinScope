"""默认只读 HTTPS 部署探针；可显式在本机隔离测试服务创建虚构账务。"""

import argparse
import secrets
import ssl
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def require(condition):
    if not condition:
        raise RuntimeError("Deployment verification failed")


def expect(response, status):
    if response.status_code != status:
        raise RuntimeError(
            "Unexpected HTTP status; inspect private service health without dumping response data"
        )
    media = response.headers.get("content-type", "").split(";")[0]
    return (
        response.json()
        if media == "application/json" or media.endswith("+json")
        else None
    )


def check(base: str, ca: Path | None = None, fixture=False):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("exact HTTPS origin required")
    if fixture and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("fixtures are restricted to explicit local test services")
    base = base.rstrip("/")
    context = (
        ssl.create_default_context(cafile=str(ca))
        if ca
        else ssl.create_default_context()
    )
    with httpx.Client(
        base_url=base,
        verify=context,
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        require(expect(client.get("/api/v1/health"), 200) == {"status": "ok"})
        require(expect(client.get("/api/v1/ready"), 200) == {"status": "ready"})
        response = client.get("/")
        expect(response, 200)
        for header in (
            "content-security-policy",
            "strict-transport-security",
            "x-content-type-options",
        ):
            if not response.headers.get(header):
                raise RuntimeError("Missing HTTPS security header")
        if response.headers.get("cache-control") != "no-store":
            raise RuntimeError("Sensitive application shell must not be cached")
        require(expect(client.get("/manifest.webmanifest"), 200)["start_url"] == "/")
        expect(client.get("/sw.js"), 200)
        expect(client.get("/api/v1/auth/session"), 401)
        expect(client.get("/api/v1/unknown"), 404)
        if not fixture:
            return
        nonce = secrets.token_hex(8)
        response = client.post(
            "/api/v1/auth/register",
            headers={"Origin": base},
            json={
                "email": f"stage9-{nonce}@example.com",
                "password": "TestOnly" + secrets.token_urlsafe(20) + "A1!",
                "display_name": "阶段9虚构测试",
            },
        )
        auth = expect(response, 201)
        cookie = response.headers.get("set-cookie", "").lower()
        if not all(
            value in cookie
            for value in (
                "__host-finscope_session=",
                "secure",
                "httponly",
                "samesite=lax",
            )
        ):
            raise RuntimeError("Production session cookie is not protected")
        headers = {"Origin": base, "X-CSRF-Token": auth["csrf_token"]}
        workspace = expect(
            client.post(
                "/api/v1/workspaces",
                headers=headers,
                json={"name": "阶段9虚构验收", "type": "personal"},
            ),
            201,
        )
        prefix = f"/api/v1/workspaces/{workspace['id']}"
        account = expect(
            client.post(
                prefix + "/finance/accounts",
                headers=headers,
                json={"name": "虚构测试账户", "opening_balance": "100.00"},
            ),
            201,
        )
        categories = {}
        for kind in ("income", "expense"):
            categories[kind] = expect(
                client.post(
                    prefix + "/finance/categories",
                    headers=headers,
                    json={"name": "虚构" + kind, "type": kind},
                ),
                201,
            )["id"]
        occurred = datetime.now(UTC).isoformat()

        def transaction(kind, amount, key, **extra):
            return client.post(
                prefix + "/finance/transactions",
                headers={**headers, "Idempotency-Key": key},
                json={
                    "account_id": account["id"],
                    "category_id": categories.get(kind),
                    "type": kind,
                    "amount": amount,
                    "occurred_at": occurred,
                    **extra,
                },
            )

        for amount in ("0.10", "0.20"):
            expect(transaction("income", amount, secrets.token_hex(12)), 201)
        expense = expect(transaction("expense", "8.88", nonce + "expense"), 201)
        require(
            expect(transaction("expense", "8.88", nonce + "expense"), 201) == expense
        )
        expect(
            transaction(
                "refund", "0.88", nonce + "refund", related_transaction_id=expense["id"]
            ),
            201,
        )
        summary = expect(client.get(prefix + "/finance/summary"), 200)
        require(
            (summary["income"], summary["expense"], summary["total_balance"])
            == (
                "0.30",
                "8.00",
                "92.30",
            )
        )
        require(
            not expect(client.get(prefix + "/analysis/capabilities"), 200)["ai_enabled"]
        )
        analysis = expect(
            client.post(
                prefix + "/analysis",
                headers={**headers, "Idempotency-Key": nonce + "analysis"},
                json={"question": "本月收支总结"},
            ),
            201,
        )

        def chunk(kind, content):
            return (
                struct.pack(">I", len(content))
                + kind
                + content
                + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
            )

        image = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\xff\xff" * 8) * 8))
            + chunk(b"IEND", b"")
        )
        expect(
            client.post(
                prefix + "/receipts",
                headers={
                    **headers,
                    "Content-Type": "application/octet-stream",
                    "X-File-Name": "fictional.png",
                },
                content=image,
            ),
            201,
        )
        expect(
            client.post(
                prefix + "/imports",
                headers={
                    **headers,
                    "Content-Type": "application/octet-stream",
                    "X-File-Name": "fictional.csv",
                },
                content=b"date,type,amount\n2026-10-24,expense,1.23\n",
            ),
            201,
        )
        expect(
            client.post(
                prefix + f"/analysis/{analysis['id']}/explain",
                headers=headers,
                json={
                    "consent": True,
                    "consent_target": "reserved",
                    "snapshot_hash": analysis["snapshot_hash"],
                },
            ),
            501,
        )
        expect(
            client.post(
                "/api/v1/workspaces",
                headers={**headers, "Origin": "https://untrusted.example"},
                json={"name": "must-not-create", "type": "personal"},
            ),
            403,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    try:
        check(args.origin, args.ca, args.fixture)
    except (
        httpx.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        KeyError,
        TypeError,
    ):
        raise SystemExit(
            "HTTPS verification failed; check certificate, host, migration and private service status"
        ) from None
    print(
        "HTTPS headers, readiness, routes and privacy checks passed"
        + (
            "; isolated fictional finance, secure cookie, CSRF and reserved AI verified"
            if args.fixture
            else " (read-only)"
        )
    )


if __name__ == "__main__":
    main()
