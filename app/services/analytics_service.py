from datetime import date, datetime
from fastapi import HTTPException

from app.utils.date_utils import get_period_range, parse_transaction_datetime, build_chart
from app.services.transaction_service import (
    load_transactions_from_humo,
    filter_transactions_by_date_range,
    calculate_summary,
    group_transactions_by_day,
    get_top_expense,
    get_last_balance,
)
from app.services.category_service import build_top_categories

VALID_PERIODS = ["day", "week", "month", "3months", "year"]

LIMIT_BY_PERIOD = {
    "day": 100,
    "week": 200,
    "month": 300,
    "3months": 500,
    "year": 500,
}


async def build_analytics(session_token: str, period: str = "week") -> dict:
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Неверный period. Допустимые: {', '.join(VALID_PERIODS)}"
        )

    start_date, end_date = get_period_range(period)
    load_limit = LIMIT_BY_PERIOD.get(period, 300)

    result = await load_transactions_from_humo(session_token, limit=load_limit)
    all_transactions = result["transactions"]

    period_transactions = filter_transactions_by_date_range(all_transactions, start_date, end_date)
    period_transactions.sort(
        key=lambda t: parse_transaction_datetime(t) or datetime.min,
        reverse=True
    )

    current_month = date.today().strftime("%Y-%m")

    return {
        "success": True,
        "screen": "analytics",
        "period": period,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "currency": "UZS",
        "summary": calculate_summary(period_transactions),
        "chart": build_chart(period_transactions, period, start_date, end_date),
        "categories": build_top_categories(period_transactions),
        "top_expense": get_top_expense(period_transactions),
        "last_balance": get_last_balance(period_transactions),
        "transaction_groups": group_transactions_by_day(period_transactions, current_month),
    }