"""Bound request bodies before JSON parsing, including chunked requests."""

import asyncio
import re
from threading import BoundedSemaphore

from starlette.responses import JSONResponse

BODY_TIMEOUT_SECONDS = 15
_body_slots = BoundedSemaphore(8)


class RequestLimitsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        async def reject(status, message):
            await JSONResponse(
                {"error": {"code": f"http_{status}", "message": message}},
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        if sum(len(k) + len(v) for k, v in scope["headers"]) > 16384:
            return await reject(431, "请求头过大")
        headers = dict(scope["headers"])
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            return await reject(415, "不接受压缩请求体")
        path = scope["path"].rstrip("/")
        maximum = 128 * 1024
        if path.startswith("/api/v1/auth/"):
            maximum = 8192
        elif re.fullmatch(r"/api/v1/workspaces/[^/]+/receipts", path):
            maximum = 5 * 1024 * 1024
        elif re.fullmatch(r"/api/v1/workspaces/[^/]+/imports", path):
            maximum = 2 * 1024 * 1024
        lengths = [v for k, v in scope["headers"] if k == b"content-length"]
        if len(lengths) > 1 or (lengths and b"transfer-encoding" in headers):
            return await reject(400, "请求长度无效")
        if lengths:
            if len(lengths[0]) > 12 or not lengths[0].isdigit():
                return await reject(400, "请求长度无效")
            if int(lengths[0]) > maximum:
                return await reject(413, "请求体过大")
        if not _body_slots.acquire(blocking=False):
            return await reject(429, "服务繁忙，请稍后重试")
        try:
            body, size = bytearray(), 0
            try:
                async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > maximum:
                            return await reject(413, "请求体过大")
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "读取请求超时")
            if lengths and size != int(lengths[0]):
                return await reject(400, "请求长度不匹配")
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, replay, send)
        finally:
            _body_slots.release()
