import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import LocalAIProvider
from app.models import AnalysisLog
from app.schemas.analysis import AnalysisPublic
from app.security.limits import WindowLimiter
from app.services.analysis_tools import ADVICE

_slots = threading.BoundedSemaphore(2)
_attempts = WindowLimiter(5, 60)


@contextmanager
def processing_slot(user_id: str):
    _attempts.consume(user_id)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "处理任务繁忙，请稍后重试")
    try:
        yield
    finally:
        _slots.release()


def digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def maintenance(db: Session, workspace_id: str, user_id: str):
    rows = db.scalars(
        select(AnalysisLog)
        .where(
            AnalysisLog.workspace_id == workspace_id,
            AnalysisLog.created_by == user_id,
            AnalysisLog.status != "expired",
        )
        .with_for_update()
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        if utc(row.expires_at) <= now:
            row.status, row.snapshot, row.selection = "expired", {}, {}
            row.target, row.error_code = "", ""
        elif (
            row.status == "running"
            and row.started_at
            and utc(row.started_at) + timedelta(seconds=90) <= now
        ):
            # 进程重启/响应丢失不自动再次付费调用；保留确定性结果。
            row.status, row.provider, row.error_code = (
                "fallback",
                "local",
                "interrupted",
            )


def get_record(
    db: Session, workspace_id: str, user_id: str, record_id: str
) -> AnalysisLog:
    row = db.scalar(
        select(AnalysisLog)
        .where(
            AnalysisLog.id == record_id,
            AnalysisLog.workspace_id == workspace_id,
            AnalysisLog.created_by == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "分析记录不存在")
    return row


def local_selection(snapshot: dict) -> dict:
    if not snapshot["facts"]:
        return {"fact_ids": [], "advice_codes": snapshot["allowed_advice"][:3]}
    return LocalAIProvider().analyze(snapshot["cloud_payload"]).model_dump()


def public(row: AnalysisLog) -> AnalysisPublic:
    snapshot = row.snapshot
    notices = list(snapshot.get("notices", []))
    if row.status == "expired":
        notices.append("此记录已清理，统计快照保留三十天；请重新提问。")
    if row.status == "fallback":
        notices.append(
            "云端不可用、返回内容未通过验证或请求中断，已使用本地结果；本次记录不会自动重试外部调用。"
        )
    if row.status == "running":
        notices.append(
            "云端正在处理，当前仍显示本地结果；可刷新此记录查看，不会重复发送。"
        )
    by_id = {fact["id"]: fact for fact in snapshot.get("facts", [])}
    observations = []
    by_code = {fact["code"]: fact for fact in snapshot.get("facts", [])}
    if snapshot.get("intent") == "profit":
        # 数值关系由固定模板引用后端差额，模型不能编造原因或重新计算。
        def ref(code):
            fact = by_code[code]
            return f"[{fact['id']}] {fact['value']} {fact['unit']}"

        observations.append(
            f"利润变化 {ref('net_change')} = 收入变化 {ref('income_change')} - 成本变化 {ref('cost_change')} - 费用变化 {ref('expense_change')}。正差额表示增加，负差额表示下降；具体业务原因需核对原始凭据。"
        )
    for key in row.selection.get("fact_ids", []):
        fact = by_id[key]
        observations.append(
            f"[{key}] {fact['label']}：{fact['value']} {fact['unit']}（{fact['start_date']} 至 {fact['end_date']}）。"
        )
    return AnalysisPublic(
        id=row.id,
        status=row.status,
        intent=snapshot.get("intent", ""),
        title=snapshot.get("title", "已清理记录"),
        provider=row.provider,
        target=row.target,
        created_at=utc(row.created_at),
        expires_at=utc(row.expires_at),
        facts=snapshot.get("facts", []),
        observations=observations,
        advice=[ADVICE[code] for code in row.selection.get("advice_codes", [])],
        notices=notices,
        tools=snapshot.get("tools", []),
        cloud_payload=snapshot.get("cloud_payload", {}),
        snapshot_hash=row.snapshot_hash,
    )
