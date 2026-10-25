"""受控只读工具；模型没有数据库句柄，也不能决定空间 ID。"""

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.money import ZERO
from app.models import Account, Category, Workspace
from app.schemas.analysis import AskRequest, Fact
from app.services.business import business_report
from app.services.finance import HK

TITLES = {
    "summary": "期间收支与资金分析",
    "categories": "支出分类排名",
    "trend": "最近三个月收支趋势",
    "growth": "费用分类增长比较",
    "profit": "收入、成本与利润变化",
    "budget": "预算数据尚未提供",
    "unsupported": "请明确问题范围",
}
ADVICE = {
    "review_categories": "打开账本核对主要支出类别，再为可调整的类别制定支出上限；这只是行动建议，系统尚未保存预算。",
    "review_trend": "逐月核对一次性支出、退款和漏记流水；未结束月份的金额不能直接与完整月份判断增减。",
    "review_profit": "分别核对营业收入、采购成本和经营费用的变化，再检查订单、采购与费用凭据；汇总数据不能证明具体业务原因。",
    "review_growth": "优先核对增长类别的原始流水与一次性项目；上期金额非正时不计算增长率。",
    "collect_data": "先补齐当前空间的账务，再用相同周期重新分析；没有记录不代表没有实际收支。",
    "review_cash": "对照应收应付与结算日期检查资金安排；经营利润与实际到账、付款可能分属不同周期。",
}


def month_start(day: date, offset: int = 0) -> date:
    ordinal = day.year * 12 + day.month - 1 + offset
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def parse_question(
    payload: AskRequest, business: bool, today: date
) -> tuple[str, date, date, list[str]]:
    question = payload.question.lower()
    notices = []
    first, last = today.replace(day=1), today
    if "上月" in question or "上个月" in question:
        first, last = month_start(today, -1), first - timedelta(days=1)
    if payload.start_date is not None:
        first, last = payload.start_date, payload.end_date
        notices.append("使用页面明确选择的日期范围，优先于问题中的相对日期。")
    if first.year < 1971 or last > today or last < first or (last - first).days >= 366:
        raise HTTPException(422, "日期须为 1971 年起至今天，起止有序且不超过 366 天")
    if any(
        word in question
        for word in (
            "删除",
            "修改",
            "新增",
            "记一笔",
            "执行",
            "sql",
            "其他空间",
            "所有空间",
        )
    ):
        intent = "unsupported"
        notices.append(
            "分析师只读取当前空间汇总，不能操作账务、执行 SQL 或跨空间查询。"
        )
    elif "预算" in question:
        intent = "budget"
        notices.append(
            "当前没有预算配置与执行数据，无法判断是否超支；可先分析支出分类并自行设定上限。"
        )
    elif any(word in question for word in ("三个月", "3个月")):
        if payload.start_date is not None:
            intent = "unsupported"
            notices.append("三个月趋势使用最近三个自然月；请清空自定义日期后重新提问。")
        else:
            intent = "trend"
            first, last = month_start(today, -2), today
    elif any(word in question for word in ("利润", "营业收入")):
        intent = "profit" if business else "unsupported"
    elif any(
        word in question for word in ("增长最快", "增长", "涨得", "增加最多")
    ) and any(word in question for word in ("类别", "分类", "费用")):
        intent = "growth"
    elif any(word in question for word in ("类别", "分类", "花钱最多", "花在哪")):
        intent = "categories"
    elif any(
        word in question
        for word in ("收入", "支出", "结余", "资金", "现金流", "总结", "分析", "余额")
    ):
        intent = "summary"
    else:
        intent = "unsupported"
    # MVP 不默默忽略不支持的日期、明细筛选或预测语义。
    if (
        payload.start_date is None
        and (
            re.search(r"\d{4}|\d+月|\d+天", question)
            or any(
                word in question
                for word in (
                    "去年",
                    "今年",
                    "本周",
                    "上周",
                    "昨天",
                    "明天",
                    "下月",
                    "预测",
                )
            )
        )
        and intent not in ("budget", "unsupported")
    ):
        intent = "unsupported"
        notices.append(
            "此日期或预测表达暂不支持；请选择自定义起止日期，或使用本月、上月、最近三个月。"
        )
    if intent == "unsupported":
        notices.append(
            "支持：期间收支总结、支出分类、最近三个月趋势、费用分类增长、商户利润变化。暂不支持指定商户、账户或单个类别筛选。"
        )
    return intent, first, last, notices


def build_snapshot(
    db: Session, workspace: Workspace, payload: AskRequest, today: date | None = None
) -> dict:
    today = today or datetime.now(HK).date()
    business = workspace.type == "business"
    intent, first, last, notices = parse_question(payload, business, today)
    names = list(
        db.scalars(select(Account.name).where(Account.workspace_id == workspace.id))
    )
    names += list(
        db.scalars(select(Category.name).where(Category.workspace_id == workspace.id))
    )
    generic = {"收入", "支出", "采购成本", "经营费用", "营业收入", "费用", "成本"}
    if intent not in ("budget", "unsupported") and (
        any(
            len(name) >= 2 and name not in generic and name in payload.question
            for name in names
        )
        or any(
            word in payload.question
            for word in ("仅看", "只看", "指定", "商家", "某个", "某家")
        )
    ):
        intent = "unsupported"
        notices.append(
            "检测到具体对象筛选，此版只支持整个空间的汇总；请改问空间收支或分类排名。"
        )
    facts: list[Fact] = []
    tools = []
    activity = False

    def report(start: date, end: date):
        nonlocal activity
        result = business_report(db, workspace.id, workspace.base_currency, start, end)
        activity = activity or bool(result.daily)
        if not result.daily:
            notices.append(
                f"{start} 至 {end} 没有有效收支或业务记录；不要将缺失记录当作已核实的零业务。"
            )
        tools.append(
            {
                "name": "period_summary",
                "start_date": str(start),
                "end_date": str(end),
                "basis": result.basis
                if business
                else "按交易发生日期统计，退款冲减原类别，内部转账不计收支，余额调整单列。",
                "formula": result.formula
                if business
                else "期间结余 = 净收入 - 净支出；净资金变动 = 结余 + 余额调整",
            }
        )
        return result

    def fact(
        code: str,
        label: str,
        value: Decimal,
        start: date,
        end: date,
        formula: str,
        unit: str | None = None,
        group: str = "",
    ):
        facts.append(
            Fact(
                id=f"F{len(facts) + 1:03}",
                code=code,
                group=group,
                label=label,
                value=format(value, ".2f"),
                unit=unit or workspace.base_currency,
                start_date=start,
                end_date=end,
                formula=formula,
            )
        )

    if intent not in ("budget", "unsupported"):
        current = report(first, last) if intent != "trend" else None
        notices.append(
            f"已解析为「{TITLES[intent]}」，范围为当前空间全部账户与相关类别，不应用单个对象筛选。"
        )
        notices.append(
            "仅基于已记录且未撤销的数据；快照生成后新增或修改的账务不会自动进入本次回答。"
        )
        if intent == "trend":
            notices.append(
                "最近三个自然月分别统计，本月仅截至今天；不把未完整月份的差额称作完整月增长。"
            )
            for offset in (-2, -1, 0):
                start = month_start(today, offset)
                end = (
                    today
                    if offset == 0
                    else month_start(today, offset + 1) - timedelta(days=1)
                )
                row = report(start, end)
                for code, label, value in (
                    ("income", "营业收入" if business else "净收入", row.revenue),
                    (
                        "expense",
                        "成本及费用" if business else "净支出",
                        row.cost + row.expense,
                    ),
                    ("net", "利润" if business else "结余", row.profit),
                ):
                    fact(
                        code,
                        f"{start:%Y-%m} {label}",
                        value,
                        start,
                        end,
                        "净额已扣除退款；净额 = 收入 - 成本 - 费用",
                    )
        elif intent == "categories":
            kinds = ("cost", "expense")
            rows = sorted(
                (row for row in current.categories if row.kind in kinds),
                key=lambda row: (-row.amount, row.category_id),
            )
            tools.append(
                {
                    "name": "expense_ranking",
                    "start_date": str(first),
                    "end_date": str(last),
                    "limit": 10,
                }
            )
            for index, row in enumerate(rows[:10], 1):
                fact(
                    "category_expense",
                    row.name,
                    row.amount,
                    first,
                    last,
                    "分类净支出，退款冲减原类别；按金额降序，含负净额",
                    group=f"C{index:03}",
                )
            notices.append("最多展示净支出最高的十个类别；退款可能使类别净额为负。")
        elif intent in ("growth", "profit"):
            # 月初起的部分月按上月相同日数比较；其他范围使用紧邻的等长期间。
            previous_end = first - timedelta(days=1)
            if first.day == 1 and first.year == last.year and first.month == last.month:
                previous_start = month_start(first, -1)
                previous_end = previous_start.replace(
                    day=min(
                        last.day,
                        calendar.monthrange(previous_start.year, previous_start.month)[
                            1
                        ],
                    )
                )
                if last.day == calendar.monthrange(last.year, last.month)[1]:
                    previous_end = first - timedelta(days=1)
            else:
                previous_start = first - timedelta(days=(last - first).days + 1)
            previous = report(previous_start, previous_end)
            notices.append(
                f"比较期：{previous_start} 至 {previous_end}；当前期：{first} 至 {last}。月初部分月比较上月相同日期；月末天数可能不同。"
            )
            if intent == "profit":
                for code, label, new, old in (
                    ("income", "营业收入", current.revenue, previous.revenue),
                    ("cost", "采购成本", current.cost, previous.cost),
                    ("expense", "经营费用", current.expense, previous.expense),
                    ("net", "利润", current.profit, previous.profit),
                ):
                    fact(
                        code,
                        f"本期{label}",
                        new,
                        first,
                        last,
                        "利润 = 营业收入 - 采购成本 - 经营费用；结算不重复计利润",
                    )
                    fact(
                        code + "_previous",
                        f"比较期{label}",
                        old,
                        previous_start,
                        previous_end,
                        "同一管理口径",
                    )
                    fact(
                        code + "_change",
                        f"{label}变化",
                        new - old,
                        first,
                        last,
                        "变化 = 本期 - 比较期",
                    )
                notices.append(
                    "利润变化 = 收入变化 - 成本变化 - 费用变化；这里只解释数值关系，不推断客流、价格或经营原因。"
                )
            else:
                kinds = ("expense",) if business else ("cost", "expense")
                old_rows = {
                    row.category_id: row
                    for row in previous.categories
                    if row.kind in kinds
                }
                new_rows = {
                    row.category_id: row
                    for row in current.categories
                    if row.kind in kinds
                }
                changes = []
                for key in new_rows.keys() | old_rows.keys():
                    row = new_rows.get(key) or old_rows[key]
                    old, new = (
                        old_rows[key].amount if key in old_rows else ZERO,
                        new_rows[key].amount if key in new_rows else ZERO,
                    )
                    rate = (
                        ((new - old) / old * 100).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        if old > ZERO
                        else None
                    )
                    changes.append((row.name, new, old, rate, key))
                changes.sort(
                    key=lambda row: (row[3] is None, -(row[3] or ZERO), row[4])
                )
                tools.append(
                    {
                        "name": "category_growth",
                        "ranking": "rate_desc_positive_baseline_first",
                        "limit": 10,
                    }
                )
                for index, (name, new, old, rate, _) in enumerate(changes[:10], 1):
                    fact(
                        "category_current",
                        f"{name} 本期",
                        new,
                        first,
                        last,
                        "分类净支出；商户仅比较经营费用，不含采购成本",
                        group=f"C{index:03}",
                    )
                    fact(
                        "category_previous",
                        f"{name} 比较期",
                        old,
                        previous_start,
                        previous_end,
                        "分类净支出，退款冲减",
                        group=f"C{index:03}",
                    )
                    fact(
                        "category_change",
                        f"{name} 变化",
                        new - old,
                        first,
                        last,
                        "本期 - 比较期",
                        group=f"C{index:03}",
                    )
                    if rate is not None:
                        fact(
                            "category_rate",
                            f"{name} 增长率",
                            rate,
                            first,
                            last,
                            "(本期 - 比较期) / 比较期 × 100%；比较期须大于零",
                            "%",
                            group=f"C{index:03}",
                        )
                notices.append(
                    "按增长率降序展示前十类；比较期非正的类别排在其后，仅展示差额，不编造增长率。负增长率表示下降。"
                )
        else:
            for code, label, value in (
                ("income", "营业收入" if business else "净收入", current.revenue),
                (
                    "expense",
                    "成本及费用" if business else "净支出",
                    current.cost + current.expense,
                ),
                ("net", "利润" if business else "结余", current.profit),
                ("cash_in", "实际流入", current.cash_in),
                ("cash_out", "实际流出", current.cash_out),
                ("adjustment", "余额调整净额", current.adjustment_net),
                ("cash_flow", "净资金变动", current.net_cash_flow),
                ("opening", "期初余额", current.opening_balance),
                ("closing", "期末余额", current.closing_balance),
            ):
                fact(
                    code,
                    label,
                    value,
                    first,
                    last,
                    (
                        (
                            "按业务确认与直接交易计入；结算不重复计利润；退款冲减原类别。"
                            if business
                            else "按交易发生日期统计，退款冲减原类别，转账与调整不计入收支。"
                        )
                        + (
                            "利润/结余 = 净收入 - 净成本 - 净费用。"
                            if code in ("income", "expense", "net")
                            else "净资金变动 = 实际流入 - 实际流出 + 余额调整；期末 = 期初 + 净资金变动。"
                        )
                    ),
                )
            if business:
                for code, label, value in (
                    ("receivable", "期末未收", current.receivable_balance),
                    ("payable", "期末未付", current.payable_balance),
                ):
                    fact(
                        code,
                        label,
                        value,
                        first,
                        last,
                        "截至期末的已确认业务减累计有效结算",
                    )
        if not activity:
            notices.append(
                "数据不足：此范围及比较期没有有效收支或业务记录，零值只表示没有已记录活动。"
            )
        if not facts:
            notices.append("数据不足：没有可排名的已记录类别。")
    advice_code = {
        "summary": "review_cash" if business else "review_categories",
        "categories": "review_categories",
        "trend": "review_trend",
        "growth": "review_growth",
        "profit": "review_profit",
    }.get(intent)
    allowed = ([advice_code] if advice_code else []) + (
        ["collect_data"] if not activity else []
    )
    serialized = [row.model_dump(mode="json") for row in facts]
    cloud = {
        "intent": intent,
        "workspace_type": workspace.type,
        "timezone": "Asia/Hong_Kong",
        "facts": [
            {
                key: row[key]
                for key in (
                    "id",
                    "code",
                    "group",
                    "value",
                    "unit",
                    "start_date",
                    "end_date",
                )
            }
            for row in serialized
        ],
        "advice_codes": allowed,
    }
    return {
        "intent": intent,
        "title": TITLES[intent],
        "facts": serialized,
        "tools": tools,
        "notices": notices,
        "allowed_advice": allowed,
        "cloud_payload": cloud,
    }
