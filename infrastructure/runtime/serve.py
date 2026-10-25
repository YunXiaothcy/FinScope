import ipaddress
import os
import sys


def main():
    trusted = os.environ.get("FINSCOPE_PROXY_ALLOW_IPS", "")
    if not trusted:
        raise RuntimeError("Trusted proxy network must be configured")
    for value in trusted.split(","):
        ipaddress.ip_network(value.strip())
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--workers",
            "1",
            "--proxy-headers",
            "--forwarded-allow-ips",
            trusted,
            "--no-access-log",
            "--no-server-header",
            "--limit-concurrency",
            "32",
            "--timeout-keep-alive",
            "5",
            "--h11-max-incomplete-event-size",
            "16384",
        ],
    )


if __name__ == "__main__":
    main()
