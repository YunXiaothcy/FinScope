"""Explicit HTTPS destinations with DNS resolution pinned to public addresses."""

import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit

import httpx


def allowed_endpoint(base: str) -> bool:
    try:
        url = urlsplit(base)
        host = url.hostname or ""
        allowed = {
            value.strip().lower()
            for value in os.getenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "").split(",")
            if value.strip()
        }
        return bool(
            len(base) <= 300
            and url.scheme == "https"
            and host in allowed
            and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            and "." in host
            and not host.endswith((".localhost", ".local", ".internal"))
            and not any((url.username, url.password, url.query, url.fragment))
            and url.port in (None, 443)
            and re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", url.path)
            and not re.fullmatch(r"[0-9.]+", host)
        )
    except ValueError:
        return False


class PublicHttpsTransport(httpx.BaseTransport):
    def __init__(self, base: str):
        if not allowed_endpoint(base):
            raise ValueError("outbound_destination_not_allowed")
        self.host = urlsplit(base).hostname
        self.inner = httpx.HTTPTransport(trust_env=False, retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.scheme != "https"
            or request.url.host != self.host
            or request.url.port not in (None, 443)
        ):
            raise httpx.ConnectError(
                "outbound_destination_not_allowed", request=request
            )
        try:
            answers = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
            if not addresses or any(
                not value.is_global
                or value.is_multicast
                or (
                    isinstance(value, ipaddress.IPv6Address)
                    and value.ipv4_mapped is not None
                )
                for value in addresses
            ):
                raise ValueError("non_public_destination")
        except (OSError, ValueError) as exc:
            raise httpx.ConnectError(
                "outbound_destination_unavailable", request=request
            ) from exc
        headers = request.headers.copy()
        headers["Host"] = self.host
        # Connect only to the checked IP, retaining hostname certificate verification.
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=str(addresses[0])),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": self.host},
        )
        return self.inner.handle_request(pinned)

    def close(self):
        self.inner.close()
