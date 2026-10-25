import json
import os
import time
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.schemas.analysis import Selection
from app.security.outbound import PublicHttpsTransport, allowed_endpoint


class AIUnavailable(Exception):
    pass


class AIProvider(Protocol):
    def analyze(self, payload: dict) -> Selection: ...


class LocalAIProvider:
    def analyze(self, payload: dict) -> Selection:
        rows = payload["facts"]
        if payload["intent"] == "profit":
            rows = sorted(
                rows,
                key=lambda row: (
                    not row["code"].endswith("_change"),
                    row["code"] != "net",
                ),
            )
        elif payload["intent"] == "summary":
            rows = sorted(
                rows,
                key=lambda row: row["code"] in ("opening", "adjustment", "cash_out"),
            )
        elif payload["intent"] == "trend":
            rows = sorted(rows, key=lambda row: row["code"] != "expense")
        return Selection(
            fact_ids=[row["id"] for row in rows[:8]],
            advice_codes=payload["advice_codes"][:3],
        )


class MockAIProvider:
    def __init__(self, selection: Selection):
        self.selection = selection
        self.calls: list[dict] = []

    def analyze(self, payload: dict) -> Selection:
        self.calls.append(payload)
        return self.selection.model_copy(deep=True)


def ai_configuration() -> dict:
    base = os.getenv("FINSCOPE_AI_BASE_URL", "").rstrip("/")
    model = os.getenv("FINSCOPE_AI_MODEL", "")
    key = os.getenv("FINSCOPE_AI_API_KEY", "")
    try:
        url = urlsplit(base)
        valid = (
            allowed_endpoint(base)
            and bool(url.hostname)
            and url.scheme == "https"
            and not any((url.username, url.password, url.query, url.fragment))
        )
    except ValueError:
        valid, url = False, urlsplit("")
    enabled = bool(valid and key and model and len(model) <= 160 and len(base) <= 300)
    # 接收方包含完整 API 基址：同一主机更换租户路径也必须重新授权。
    return {
        "enabled": enabled,
        "base": base,
        "key": key,
        "model": model,
        "target": f"{base} / {model}" if enabled else "",
    }


def validate_selection(value: Selection, payload: dict) -> Selection:
    value = Selection.model_validate(value.model_dump())
    valid_ids = {fact["id"] for fact in payload["facts"]}
    if not set(value.fact_ids) <= valid_ids or not set(value.advice_codes) <= set(
        payload["advice_codes"]
    ):
        raise AIUnavailable("invalid_references")
    if len(set(value.fact_ids)) != len(value.fact_ids) or len(
        set(value.advice_codes)
    ) != len(value.advice_codes):
        raise AIUnavailable("duplicate_references")
    return value


class CompatibleAIProvider:
    def __init__(self, config: dict, transport: httpx.BaseTransport | None = None):
        self.config, self.transport = config, transport

    def analyze(self, payload: dict) -> Selection:
        if not self.config["enabled"]:
            raise AIUnavailable("not_configured")
        system = (
            "你是财务指标分析重点选择器。只根据给定的已计算证据选出与intent最相关的fact_ids，"
            "优先选择变化、排名及需要核对的项目。建议只从advice_codes选择。"
            "不得生成文字、数字、代码、SQL、工具调用或推测原因。不得修改数据或计算金额。"
            "分类已匿名化，同一group表示同一类别；不要推断身份。"
            "只返回符合此JSON Schema的JSON对象："
            + json.dumps(Selection.model_json_schema(), ensure_ascii=False)
        )
        try:
            started = time.monotonic()
            with (
                httpx.Client(
                    timeout=httpx.Timeout(20, connect=5, read=10),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport
                    or PublicHttpsTransport(self.config["base"]),
                ) as client,
                client.stream(
                    "POST",
                    self.config["base"] + "/chat/completions",
                    headers={
                        "Authorization": "Bearer " + self.config["key"],
                        "Accept-Encoding": "identity",
                    },
                    json={
                        "model": self.config["model"],
                        "messages": [
                            {"role": "system", "content": system},
                            {
                                "role": "user",
                                "content": json.dumps(payload, ensure_ascii=False),
                            },
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 600,
                    },
                ) as response,
            ):
                response.raise_for_status()
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise ValueError("compressed_response_not_allowed")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > 32768 or time.monotonic() - started > 25:
                        raise ValueError("response_limit")
            message = json.loads(body)["choices"][0]["message"]
            if (
                message.get("tool_calls")
                or message.get("refusal")
                or not isinstance(message["content"], str)
                or len(message["content"]) > 6000
            ):
                raise ValueError("invalid_response")
            return validate_selection(
                Selection.model_validate_json(message["content"]), payload
            )
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            ValidationError,
        ) as exc:
            raise AIUnavailable("unavailable_or_invalid") from exc
