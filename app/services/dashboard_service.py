from datetime import date, datetime
from typing import Optional
from fastapi import HTTPException

from app.core.constants import RU_MONTHS
from app.utils.date_utils import get_month_range, get_previous_month, parse_transaction_datetime
from app.services.transaction_service import (
    load_transactions_from_humo,
    filter_transactions_by_date_range,
    calculate_summary,
    group_transactions_by_day,
    get_top_expense,
    get_last_balance,
)
from app.services.category_service import build_top_categories


def build_month_insight(current: dict, previous: Optional[dict]) -> dict:
    if previous is None or previous["expense_total"] == 0:
        return {"type": "info", "text": "Первый месяц с данными — сравнение недоступно", "percent": 0, "direction": "new"}

    curr_exp = current["expense_total"]
    prev_exp = previous["expense_total"]

    if prev_exp == 0:
        return {"type": "info", "text": "В прошлом месяце не было расходов", "percent": 0, "direction": "none"}

    diff_percent = round((curr_exp - prev_exp) / prev_exp * 100, 1)

    if diff_percent > 20:
        return {"type": "warning", "text": f"Расходы выросли на {abs(diff_percent)}% по сравнению с прошлым месяцем", "percent": abs(diff_percent), "direction": "up"}
    elif diff_percent < -10:
        return {"type": "success", "text": f"Расходы снизились на {abs(diff_percent)}% — отличный результат!", "percent": abs(diff_percent), "direction": "down"}
    else:
        return {"type": "neutral", "text": "Расходы примерно на том же уровне, что и в прошлом месяце", "percent": abs(diff_percent), "direction": "same"}


async def build_dashboard(session_token: str, month: Optional[str] = None, limit: int = 500) -> dict:
    today = date.today()
    current_month = today.strftime("%Y-%m")

    if month is None:
        month = current_month

    # Валидация формата
    try:
        year, mon = int(month[:4]), int(month[5:7])
        date(year, mon, 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Неверный формат month. Используйте YYYY-MM")

    # Запрет будущего месяца
    if date(year, mon, 1) > date(today.year, today.month, 1):
        raise HTTPException(status_code=400, detail="Нельзя запрашивать будущий месяц")

    month_title = f"{RU_MONTHS[mon]} {year}"
    start_date, end_date = get_month_range(month)

    result = await load_transactions_from_humo(session_token, limit=limit)
    all_transactions = result["transactions"]

    month_transactions = filter_transactions_by_date_range(all_transactions, start_date, end_date)
    month_transactions.sort(
        key=lambda t: parse_transaction_datetime(t) or datetime.min,
        reverse=True
    )

    current_summary = calculate_summary(month_transactions)

    previous_month = get_previous_month(month)
    prev_start, prev_end = get_month_range(previous_month)
    prev_transactions = filter_transactions_by_date_range(all_transactions, prev_start, prev_end)
    previous_summary = calculate_summary(prev_transactions) if prev_transactions else None

    return {
        "success": True,
        "screen": "dashboard",
        "month": month,
        "month_title": month_title,
        "can_go_next_month": date(year, mon, 1) < date(today.year, today.month, 1),
        "can_go_previous_month": True,
        "currency": "UZS",
        "summary": current_summary,
        "top_categories": build_top_categories(month_transactions),
        "top_expense": get_top_expense(month_transactions),
        "last_balance": get_last_balance(month_transactions),
        "insight": build_month_insight(current_summary, previous_summary),
        "transaction_groups": group_transactions_by_day(month_transactions, month),
        "has_more": result["has_more"],
        "next_offset_id": result["next_offset_id"],
    }