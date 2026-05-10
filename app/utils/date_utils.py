from datetime import datetime, date, timedelta
from calendar import monthrange
from typing import Optional
from app.core.constants import RU_MONTHS, RU_MONTHS_GENITIVE, RU_WEEKDAYS, RU_WEEKDAYS_SHORT


def parse_transaction_datetime(tx: dict) -> Optional[datetime]:
    """
    Парсит datetime из транзакции.
    Если есть date + time — парсит оба.
    Если time нет — использует 00:00.
    """
    if tx.get("datetime"):
        try:
            return datetime.fromisoformat(tx["datetime"])
        except Exception:
            pass

    date_str = tx.get("date")
    time_str = tx.get("time", "00:00") or "00:00"

    if date_str:
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        except Exception:
            pass
    return None


def get_month_range(month: str) -> tuple:
    """Возвращает (start_date, end_date) для месяца формата YYYY-MM."""
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    last_day = monthrange(year, mon)[1]
    end = date(year, mon, last_day)
    return start, end


def get_period_range(period: str) -> tuple:
    """Возвращает (start_date, end_date) для периода аналитики."""
    today = date.today()

    if period == "day":
        return today, today

    elif period == "week":
        start = today - timedelta(days=today.weekday())
        return start, today

    elif period == "month":
        start = date(today.year, today.month, 1)
        return start, today

    elif period == "3months":
        # Календарные 3 месяца назад
        month = today.month - 2
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        return start, today

    elif period == "year":
        start = date(today.year, 1, 1)
        return start, today

    else:
        return today - timedelta(days=7), today


def get_previous_month(month: str) -> str:
    """Возвращает предыдущий месяц в формате YYYY-MM."""
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def build_day_label(tx_date: date, selected_month: str) -> str:
    """Возвращает красивый лейбл для группы дня."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    current_month = today.strftime("%Y-%m")

    if selected_month == current_month:
        if tx_date == today:
            return "Сегодня"
        if tx_date == yesterday:
            return "Вчера"

    weekday = RU_WEEKDAYS[tx_date.weekday()]
    day = tx_date.day
    month_name = RU_MONTHS_GENITIVE[tx_date.month]
    return f"{weekday}, {day} {month_name}"


def build_chart(transactions: list, period: str, start: date, end: date) -> list:
    """Строит данные для графика."""

    if period == "day":
        chart = {
            h: {"label": f"{h:02d}:00", "date": start.isoformat(), "income": 0.0, "expense": 0.0}
            for h in range(24)
        }
        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if dt:
                h = dt.hour
                if tx["type"] == "income":
                    chart[h]["income"] += tx["amount"]
                else:
                    chart[h]["expense"] += tx["amount"]
        return [
            {"label": v["label"], "date": v["date"], "income": round(v["income"], 2), "expense": round(v["expense"], 2)}
            for v in chart.values()
        ]

    elif period in ("week", "month"):
        chart = {}
        current = start
        while current <= end:
            key = current.isoformat()
            label = RU_WEEKDAYS_SHORT[current.weekday()] if period == "week" else str(current.day)
            chart[key] = {"label": label, "date": key, "income": 0.0, "expense": 0.0}
            current += timedelta(days=1)

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if dt:
                key = dt.date().isoformat()
                if key in chart:
                    if tx["type"] == "income":
                        chart[key]["income"] += tx["amount"]
                    else:
                        chart[key]["expense"] += tx["amount"]

        return [
            {"label": v["label"], "date": v["date"], "income": round(v["income"], 2), "expense": round(v["expense"], 2)}
            for v in chart.values()
        ]

    else:
        # 3months, year — по месяцам
        chart = {}
        current = date(start.year, start.month, 1)
        while current <= end:
            key = current.strftime("%Y-%m")
            chart[key] = {"label": RU_MONTHS[current.month], "date": key, "income": 0.0, "expense": 0.0}
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if dt:
                key = dt.strftime("%Y-%m")
                if key in chart:
                    if tx["type"] == "income":
                        chart[key]["income"] += tx["amount"]
                    else:
                        chart[key]["expense"] += tx["amount"]

        return [
            {"label": v["label"], "date": v["date"], "income": round(v["income"], 2), "expense": round(v["expense"], 2)}
            for v in chart.values()
        ]