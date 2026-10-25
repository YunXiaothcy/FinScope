from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.money import ZERO
from app.models import Workspace
from app.schemas.analytics import DashboardDay, DashboardPublic
from app.services.business import business_report
from app.services.finance import balances, period_bounds


def dashboard(
    db: Session, workspace: Workspace, start_date: date | None, end_date: date | None
) -> DashboardPublic:
    first, last, _, _ = period_bounds(start_date, end_date)
    if (last - first).days >= 366:
        raise HTTPException(422, "仪表盘每次最多查询 366 天，请缩短日期范围")
    # 复用已验证的退款、业务确认与结算口径，避免图表另算一套账。
    report = business_report(db, workspace.id, workspace.base_currency, first, last)
    business = workspace.type == "business"
    by_date = {row.date: row for row in report.daily}
    running = report.opening_balance
    daily = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        row = by_date.get(day)
        income = row.revenue if row else ZERO
        expense = row.cost + row.expense if row else ZERO
        flow = row.net_cash_flow if row else ZERO
        running += flow
        daily.append(
            DashboardDay(
                date=day,
                income=income,
                expense=expense,
                net=income - expense,
                cash_in=row.cash_in if row else ZERO,
                cash_out=row.cash_out if row else ZERO,
                adjustment_net=row.adjustment_net if row else ZERO,
                net_cash_flow=flow,
                closing_balance=running,
            )
        )
    return DashboardPublic(
        workspace_type=workspace.type,
        currency=report.currency,
        start_date=first,
        end_date=last,
        basis=report.basis
        if business
        else "按交易发生日期统计；退款冲减原收入或支出，内部转账不计收支，余额调整单列。",
        formula=report.formula
        if business
        else "期间结余 = 净收入 - 净支出；净资金变动 = 结余 + 余额调整",
        has_activity=bool(report.daily),
        income=report.revenue,
        expense=report.cost + report.expense,
        net=report.profit,
        cost=report.cost if business else None,
        operating_expense=report.expense if business else None,
        cash_in=report.cash_in,
        cash_out=report.cash_out,
        adjustment_net=report.adjustment_net,
        net_cash_flow=report.net_cash_flow,
        opening_balance=report.opening_balance,
        closing_balance=report.closing_balance,
        current_balance=report.current_balance,
        receivable_balance=report.receivable_balance if business else None,
        payable_balance=report.payable_balance if business else None,
        accounts=balances(db, workspace.id),
        categories=[
            {
                "category_id": item.category_id,
                "name": item.name,
                "kind": item.kind
                if business
                else "income"
                if item.kind == "revenue"
                else "expense",
                "amount": item.amount,
            }
            for item in report.categories
        ],
        daily=daily,
    )
