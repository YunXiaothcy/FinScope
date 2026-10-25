"""生成不含明文凭据的 Compose 环境文件，密码单独留在私有服务端文件。"""

import argparse
import ipaddress
import os
import re
import secrets
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def private_write(path: Path, content: str):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    path.chmod(0o600)


def prepare(
    directory: Path,
    domain: str,
    local: bool = False,
    public: bool = False,
    https_port: int = 443,
    http_port: int = 80,
):
    if (
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain)
        or ".." in domain
    ):
        raise ValueError("domain must be one ASCII hostname")
    if not local and (
        domain == "localhost"
        or "." not in domain
        or domain.endswith((".example", ".invalid"))
    ):
        raise ValueError("a real domain is required outside local validation")
    try:
        ipaddress.ip_address(domain)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip and not local:
        raise ValueError("use a DNS name for the deployment target")
    if (
        any(not 1 <= port <= 65535 for port in (http_port, https_port))
        or http_port == https_port
    ):
        raise ValueError("distinct valid ports are required")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    for child in ("secrets", "certs", "backups"):
        (directory / child).mkdir(mode=0o700)
    private_write(
        directory / "secrets/admin_password", secrets.token_urlsafe(36) + "\n"
    )
    private_write(directory / "secrets/app_password", secrets.token_urlsafe(36) + "\n")
    origin = f"https://{domain}" + (f":{https_port}" if https_port != 443 else "")
    uid = os.getuid() if hasattr(os, "getuid") else 10001
    gid = os.getgid() if hasattr(os, "getgid") else 10001
    if uid == 0:
        uid = gid = 10001
    values = {
        "FINSCOPE_DOMAIN": domain,
        "FINSCOPE_PUBLIC_ORIGIN": origin,
        "FINSCOPE_BIND_ADDRESS": "0.0.0.0" if public else "127.0.0.1",
        "FINSCOPE_HTTP_PORT": str(http_port),
        "FINSCOPE_HTTPS_PORT": str(https_port),
        "FINSCOPE_DEPLOY_DIR": str(directory).replace("\\", "/"),
        "FINSCOPE_UID": str(uid),
        "FINSCOPE_GID": str(gid),
        "FINSCOPE_PROXY_SUBNET": "172.30.80.0/24",
        "FINSCOPE_RELEASE": "local",
        "FINSCOPE_DATABASE_NAME": "finscope",
    }
    private_write(
        directory / ".env.production",
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
    )
    if hasattr(os, "getuid") and os.getuid() == 0:
        # 新建目录范围内修正所有权，让非 root 容器能读私有文件、写备份。
        for path in (
            directory,
            directory / "secrets",
            directory / "certs",
            directory / "backups",
            directory / "secrets/admin_password",
            directory / "secrets/app_password",
            directory / ".env.production",
        ):
            os.chown(path, uid, gid)
    return values


def check(directory: Path, require_certificate: bool = True):
    config = dict(
        line.split("=", 1)
        for line in (directory / ".env.production")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    for name in ("admin_password", "app_password"):
        secret = directory / "secrets" / name
        if not secret.is_file() or len(secret.read_text(encoding="utf-8").strip()) < 32:
            raise ValueError("private database password file is missing or too short")
        if os.name != "nt" and secret.stat().st_mode & 0o077:
            raise ValueError("secret file must not be readable by group/others")
        if os.name != "nt" and secret.stat().st_uid != int(config["FINSCOPE_UID"]):
            raise ValueError("secret owner must match container UID")
    if not (directory / ".env.production").is_file():
        raise ValueError("private Compose environment file is missing")
    if require_certificate:
        key = directory / "certs/privkey.pem"
        if os.name != "nt" and (
            key.stat().st_mode & 0o077
            or key.stat().st_uid != int(config["FINSCOPE_UID"])
        ):
            raise ValueError("private key permissions/owner must match container UID")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            str(directory / "certs/fullchain.pem"), str(directory / "certs/privkey.pem")
        )
    return True


def render_nginx(output: Path, values: dict):
    template = (ROOT / "infrastructure/nginx/nginx.conf.template").read_text(
        encoding="utf-8"
    )

    def substitute(match):
        key = match.group(1)
        value = str(values[key])
        if any(
            character in value for character in ('"', "\r", "\n", ";", "{", "}", "$")
        ):
            raise ValueError("unsafe nginx configuration value")
        return value.replace("\\", "/")

    output.write_text(
        re.sub(r"\$\{(FINSCOPE_[A-Z_]+)\}", substitute, template),
        encoding="utf-8",
        newline="\n",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/deploy")
    parser.add_argument("--domain")
    parser.add_argument("--local", action="store_true")
    parser.add_argument(
        "--public",
        action="store_true",
        help="explicitly publish the frontend ports on all interfaces",
    )
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            check(args.directory.resolve())
            print(
                "Private files and certificate/key match OK; public trust/expiry still require an HTTPS probe"
            )
        else:
            if not args.domain:
                parser.error("--domain is required")
            prepare(
                args.directory,
                args.domain,
                args.local,
                args.public,
                args.https_port,
                args.http_port,
            )
            print(
                "Private deployment files created; install certificate and key before starting HTTPS"
            )
    except (OSError, ValueError, ssl.SSLError):
        raise SystemExit(
            "Deployment preparation failed: check directory, domain, permissions and certificate; no credentials printed"
        ) from None


if __name__ == "__main__":
    main()
