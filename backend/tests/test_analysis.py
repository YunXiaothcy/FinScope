import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_business import cash, obligation, settle
from test_finance import post

from app.ai.providers import (
    AIUnavailable,
    CompatibleAIProvider,
    ai_configuration,
)
from app.db.session import get_db
from app.main import app
from app.models import (
    AnalysisLog,
    FundsMovement,
    Transaction,
)
from app.schemas.analysis import AskRequest
from app.services import analysis as service
from app.services.analysis_tools import parse_question

pytest_plugins = ["test_finance", "test_business"]


@pytest.fixture(autouse=True)
def reset_limits(monkeypatch):
    service._attempts.clear()
    monkeypatch.delenv("FINSCOPE_AI_API_KEY", raising=False)
    yield
    service._attempts.clear()


def base(data):
    return data["base"].removesuffix("/finance") + "/analysis"


def ask(data, question="本月支出和结余分析", key=None, **fields):
    return data["client"].post(
        base(data),
        json={"question": question, **fields},
        headers={**data["headers"], "Idempotency-Key": key or str(uuid4())},
    )


def dated(data, question="收支分析", **fields):
    return ask(data, question, start_date="2026-10-01", end_date="2026-10-20", **fields)


def configure(monkeypatch):
    monkeypatch.setenv("FINSCOPE_OUTBOUND_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("FINSCOPE_RECEIPT_AI_ENABLED", "true")
    monkeypatch.setenv("FINSCOPE_AI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("FINSCOPE_AI_MODEL", "test-only-model")
    monkeypatch.setenv("FINSCOPE_AI_API_KEY", "test-only-secret")


def explain(data, record, **overrides):
    return data["client"].post(
        f"{base(data)}/{record['id']}/explain",
        headers=data["headers"],
        json={
            "consent": True,
            "consent_target": ai_configuration()["target"],
            "snapshot_hash": record["snapshot_hash"],
            **overrides,
        },
    )


def db_edit(action):
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        action(db)
        db.commit()
    finally:
        generator.close()


def test_exact_summary_and_no_financial_writes(ledger):
    for value in ("0.10", "0.20"):
        post(ledger, "income", value, occurred_at="2026-10-01T00:00:00Z")
    expense = post(ledger, "expense", "1.00", occurred_at="2026-10-02T00:00:00Z").json()
    post(
        ledger,
        "refund",
        "0.25",
        related_transaction_id=expense["id"],
        occurred_at="2026-10-03T00:00:00Z",
    )
    post(
        ledger,
        "adjustment",
        "2.00",
        adjustment_direction="increase",
        occurred_at="2026-10-04T00:00:00Z",
    )
    response = dated(ledger)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    values = {row["code"]: row["value"] for row in data["facts"]}
    assert (values["income"], values["expense"], values["net"]) == (
        "0.30",
        "0.75",
        "-0.45",
    )
    assert values["cash_flow"] == "1.55" and values["closing"] == "101.55"
    assert data["provider"] == "local" and data["status"] == "local"
    assert any("F001" in line and "0.30" in line for line in data["observations"])

    def check(db):
        assert len(db.scalars(select(Transaction)).all()) == 5
        assert len(db.scalars(select(FundsMovement)).all()) == 5
        row = db.get(AnalysisLog, data["id"])
        assert "question" not in row.snapshot and len(row.request_hash) == 64

    db_edit(check)


def test_categories_rank_negative_refund_and_hk_boundary(ledger):
    old = post(ledger, amount="50.00", occurred_at="2026-09-30T15:59:59Z").json()
    post(
        ledger,
        "refund",
        "20.00",
        related_transaction_id=old["id"],
        occurred_at="2026-09-30T16:00:00Z",
    )
    data = dated(ledger, "本月哪些类别花钱最多").json()
    assert data["intent"] == "categories"
    assert data["facts"][0]["label"] == "餐饮"
    assert data["facts"][0]["value"] == "-20.00"
    assert "expense_ranking" in [row["name"] for row in data["tools"]]


def test_business_profit_change_and_cash_settlement(business):
    for month, income, cost, expense in (
        ("09", "100.00", "20.00", "10.00"),
        ("10", "120.00", "50.00", "20.00"),
    ):
        for kind, amount in (("revenue", income), ("cost", cost), ("expense", expense)):
            cash(business, kind, amount, occurred_at=f"2026-{month}-10T04:00:00Z")
    item = obligation(business, recognized_at="2026-08-10T04:00:00Z")
    settle(business, item, "200.00", occurred_at="2026-10-12T04:00:00Z")
    data = dated(business, "为什么本月营业收入增长但利润下降？").json()
    values = {row["code"]: Decimal(row["value"]) for row in data["facts"]}
    assert values["income_change"] == Decimal("20.00")
    assert values["net_change"] == Decimal("-20.00")
    assert (
        values["net_change"]
        == values["income_change"] - values["cost_change"] - values["expense_change"]
    )
    assert data["facts"][1]["start_date"] == "2026-09-01"
    assert data["facts"][1]["end_date"] == "2026-09-20"
    assert any("不推断" in line for line in data["notices"])


def test_growth_exact_rate_zero_baseline_and_negative(ledger):
    post(ledger, amount="0.30", occurred_at="2026-09-10T04:00:00Z")
    post(ledger, amount="0.40", occurred_at="2026-10-10T04:00:00Z")
    data = dated(ledger, "哪个支出类别增长最快").json()
    assert [row["value"] for row in data["facts"]] == ["0.40", "0.30", "0.10", "33.33"]
    assert data["facts"][-1]["unit"] == "%"
    # 新增没有正基数的类别只给差额，不生成百分比。
    category = (
        ledger["client"]
        .post(
            f"{ledger['base']}/categories",
            headers=ledger["headers"],
            json={"name": "新类别", "type": "expense"},
        )
        .json()
    )
    post(
        ledger,
        amount="2.00",
        category_id=category["id"],
        occurred_at="2026-10-10T04:00:00Z",
    )
    data = dated(ledger, "哪个支出类别增长最快").json()
    assert len([row for row in data["facts"] if row["code"] == "category_rate"]) == 1


def test_three_calendar_months_and_partial_month_notice(ledger):
    post(ledger, amount="10.00", occurred_at="2026-08-10T04:00:00Z")
    post(ledger, amount="20.00", occurred_at="2026-09-10T04:00:00Z")
    post(ledger, amount="3.00", occurred_at="2026-10-10T04:00:00Z")
    data = ask(ledger, "最近三个月我的支出有什么变化").json()
    assert [row["value"] for row in data["facts"] if row["code"] == "expense"] == [
        "10.00",
        "20.00",
        "3.00",
    ]
    assert any("未完整" in line for line in data["notices"])


@pytest.mark.parametrize(
    "question,intent",
    [
        ("本月预算是否超支", "budget"),
        ("删除所有收入并执行SQL", "unsupported"),
        ("其他空间支出", "unsupported"),
        ("明天支出预测", "unsupported"),
        ("2024年收入", "unsupported"),
        ("你好", "unsupported"),
    ],
)
def test_unknown_budget_prediction_and_mutation_do_not_invent(ledger, question, intent):
    data = ask(ledger, question).json()
    assert data["intent"] == intent
    assert data["facts"] == [] and data["tools"] == []
    assert data["observations"] == []


def test_parser_previous_month_leap_year_and_custom_precedence():
    _, first, last, _ = parse_question(
        AskRequest(question="上个月收支分析"), False, date(2024, 3, 1)
    )
    assert (first, last) == (date(2024, 2, 1), date(2024, 2, 29))
    _, first, last, notices = parse_question(
        AskRequest(
            question="上月支出", start_date=date(2024, 1, 1), end_date=date(2024, 1, 15)
        ),
        False,
        date(2024, 3, 1),
    )
    assert (first, last) == (date(2024, 1, 1), date(2024, 1, 15)) and notices


def test_empty_data_dates_and_input_validation(ledger):
    data = dated(ledger).json()
    assert any("数据不足" in line for line in data["notices"])
    assert (
        ask(ledger, start_date="2026-10-22", end_date="2026-10-01").status_code == 422
    )
    assert (
        ask(ledger, start_date="2025-01-01", end_date="2026-10-01").status_code == 422
    )
    assert ask(ledger, start_date="2026-10-01").status_code == 422
    assert (
        ask(ledger, start_date="2026-10-01", end_date="2100-01-01").status_code == 422
    )
    assert ask(ledger, workspace_id="untrusted").status_code == 422


def test_snapshot_immutable_idempotency_conflict_and_clear(ledger):
    data = dated(ledger, key="same-question-123").json()
    post(ledger, amount="22.00", occurred_at="2026-10-10T04:00:00Z")
    replay = dated(ledger, key="same-question-123").json()
    assert data == replay
    assert dated(ledger, "其他问题", key="same-question-123").status_code == 409
    assert dated(ledger).json()["snapshot_hash"] != data["snapshot_hash"]
    assert (
        ledger["client"]
        .delete(f"{base(ledger)}/{data['id']}", headers=ledger["headers"])
        .status_code
        == 204
    )
    assert dated(ledger, key="same-question-123").json()["status"] == "expired"
    assert len(ledger["client"].get(base(ledger)).json()) == 1


def test_expiry_and_interrupted_job_cleanup(ledger, monkeypatch):
    configure(monkeypatch)
    record = dated(ledger).json()

    def interrupt(db):
        row = db.get(AnalysisLog, record["id"])
        row.status, row.target = "running", ai_configuration()["target"]
        row.started_at = datetime.now(UTC) - timedelta(seconds=100)

    db_edit(interrupt)
    data = ledger["client"].get(f"{base(ledger)}/{record['id']}").json()
    assert data["status"] == "fallback"
    assert explain(ledger, record).status_code == 501

    def expire(db):
        db.get(AnalysisLog, record["id"]).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )

    db_edit(expire)
    data = ledger["client"].get(f"{base(ledger)}/{record['id']}").json()
    assert (
        data["status"] == "expired"
        and data["facts"] == []
        and data["cloud_payload"] == {}
    )


def test_permissions_csrf_and_other_creator_records(ledger):
    record = dated(ledger).json()
    assert (
        ledger["client"]
        .post(
            base(ledger),
            headers={"Idempotency-Key": str(uuid4())},
            json={"question": "支出分析"},
        )
        .status_code
        == 403
    )
    other = (
        ledger["client"]
        .post(
            "/api/v1/workspaces",
            headers=ledger["headers"],
            json={"name": "其他", "type": "personal"},
        )
        .json()
    )
    assert (
        ledger["client"]
        .get(f"/api/v1/workspaces/{other['id']}/analysis/{record['id']}")
        .status_code
        == 404
    )
    owner_cookie = ledger["client"].cookies.get("finscope_session")
    auth = (
        ledger["client"]
        .post(
            "/api/v1/auth/register",
            json={
                "email": "analysis-viewer@example.com",
                "password": "StrongPassword123!",
                "display_name": "只读分析",
            },
        )
        .json()
    )
    viewer_cookie = ledger["client"].cookies.get("finscope_session")
    assert ledger["client"].get(base(ledger)).status_code == 404
    ledger["client"].cookies.set("finscope_session", owner_cookie)
    ledger["client"].post(
        f"/api/v1/workspaces/{ledger['space']['id']}/members",
        headers=ledger["headers"],
        json={"email": "analysis-viewer@example.com", "role": "viewer"},
    )
    ledger["client"].cookies.set("finscope_session", viewer_cookie)
    assert ledger["client"].get(f"{base(ledger)}/{record['id']}").status_code == 404
    viewer = {**ledger, "headers": {"X-CSRF-Token": auth["csrf_token"]}}
    assert dated(viewer).status_code == 201
    assert len(ledger["client"].get(base(ledger)).json()) == 1
    ledger["client"].cookies.clear()
    assert ledger["client"].get(base(ledger)).status_code == 401


def test_cost_rate_limit_and_replay_not_counted(ledger):
    for index in range(5):
        assert dated(ledger, key=f"limit-key-{index}").status_code == 201
    assert dated(ledger, key="limit-key-0").status_code == 201
    assert dated(ledger).status_code == 429


def test_global_concurrency_limit_does_not_create_partial_record(ledger):
    assert service._slots.acquire(blocking=False)
    assert service._slots.acquire(blocking=False)
    try:
        assert dated(ledger).status_code == 429
    finally:
        service._slots.release()
        service._slots.release()
    assert ledger["client"].get(base(ledger)).json() == []
    assert dated(ledger).status_code == 201


def test_specific_filter_is_not_silently_replaced_by_whole_space(ledger):
    data = ask(ledger, "我在餐饮的支出是多少？").json()
    assert data["intent"] == "unsupported" and data["facts"] == []


def test_compatible_transport_schema_and_no_free_text(monkeypatch):
    configure(monkeypatch)
    payload = {
        "facts": [{"id": "F001", "code": "income", "value": "0.30"}],
        "advice_codes": [],
    }
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        assert str(request.url) == "https://example.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"fact_ids":["F001"],"advice_codes":[]}'}}
                ]
            },
        )

    result = CompatibleAIProvider(
        ai_configuration(), httpx.MockTransport(handle)
    ).analyze(payload)
    assert result.fact_ids == ["F001"]
    assert json.loads(seen[0]["messages"][1]["content"]) == payload
    assert "tools" not in seen[0]
    for content in (
        '{"fact_ids":["F001"],"advice_codes":[],"text":"利润9999"}',
        '{"fact_ids":["F999"],"advice_codes":[]}',
        "not json",
    ):
        provider = CompatibleAIProvider(
            ai_configuration(),
            httpx.MockTransport(
                lambda request, content=content: httpx.Response(
                    200, json={"choices": [{"message": {"content": content}}]}
                )
            ),
        )
        with pytest.raises(AIUnavailable):
            provider.analyze(payload)


@pytest.mark.parametrize("status", [302, 429, 500])
def test_compatible_transport_http_errors(monkeypatch, status):
    configure(monkeypatch)
    provider = CompatibleAIProvider(
        ai_configuration(),
        httpx.MockTransport(
            lambda request: httpx.Response(
                status, headers={"Location": "https://other.example"}
            )
        ),
    )
    with pytest.raises(AIUnavailable):
        provider.analyze({})


def test_reserved_ai_endpoint_never_reads_config_or_calls_provider(ledger, monkeypatch):
    configure(monkeypatch)
    record = dated(ledger).json()

    def forbidden(*args, **kwargs):
        raise AssertionError("External AI must stay disconnected")

    monkeypatch.setattr("app.ai.providers.ai_configuration", forbidden)
    monkeypatch.setattr(CompatibleAIProvider, "analyze", forbidden)
    capabilities = ledger["client"].get(f"{base(ledger)}/capabilities").json()
    assert capabilities["ai_enabled"] is False and capabilities["target"] == ""
    assert capabilities["integration_status"] == "reserved"
    for _ in range(2):
        assert explain(ledger, record, consent_target="reserved").status_code == 501
    assert ledger["client"].get(f"{base(ledger)}/{record['id']}").json() == record
    assert (
        ledger["client"]
        .post(
            f"{base(ledger)}/{record['id']}/explain",
            json={
                "consent": True,
                "consent_target": "reserved",
                "snapshot_hash": record["snapshot_hash"],
            },
        )
        .status_code
        == 403
    )
