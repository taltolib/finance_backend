import re
from datetime import datetime, date, timedelta
from typing import Optional
from fastapi import HTTPException

from app.services.telegram_client_service import create_client, connect_client, disconnect_client, ensure_authorized
from app.services.humo_parser_service import parse_humo_message
from app.services.category_service import detect_category, build_top_categories
from app.utils.date_utils import parse_transaction_datetime, build_day_label
from app.core.constants import RU_WEEKDAYS


def normalize_transaction(tx: dict) -> dict:
    """Добавляет category и category_title в транзакцию."""
    cat_id, cat_title = detect_category(tx)
    tx["category"] = cat_id
    tx["category_title"] = cat_title
    return tx


def filter_transactions_by_date_range(transactions: list, start: date, end: date) -> list:
    result = []
    for tx in transactions:
        dt = parse_transaction_datetime(tx)
        if dt is None:
            continue
        if start <= dt.date() <= end:
            result.append(tx)
    return result


def calculate_summary(transactions: list) -> dict:
    incomes = [t for t in transactions if t["type"] == "income"]
    expenses = [t for t in transactions if t["type"] == "expense"]
    income_total = sum(t["amount"] for t in incomes)
    expense_total = sum(t["amount"] for t in expenses)
    return {
        "income_total": round(income_total, 2),
        "expense_total": round(expense_total, 2),
        "net_total": round(income_total - expense_total, 2),
        "transactions_count": len(transactions),
        "income_count": len(incomes),
        "expense_count": len(expenses),
        "average_expense": round(expense_total / len(expenses), 2) if expenses else 0,
        "average_income": round(income_total / len(incomes), 2) if incomes else 0,
    }


def group_transactions_by_day(transactions: list, selected_month: str) -> list:
    groups: dict = {}
    for tx in transactions:
        dt = parse_transaction_datetime(tx)
        if dt is None:
            continue
        tx_date = dt.date()
        date_key = tx_date.isoformat()
        if date_key not in groups:
            groups[date_key] = {
                "label": build_day_label(tx_date, selected_month),
                "date": date_key,
                "weekday": RU_WEEKDAYS[tx_date.weekday()],
                "income_total": 0.0,
                "expense_total": 0.0,
                "transactions_count": 0,
                "transactions": [],
            }
        groups[date_key]["transactions"].append(tx)
        groups[date_key]["transactions_count"] += 1
        if tx["type"] == "income":
            groups[date_key]["income_total"] += tx["amount"]
        else:
            groups[date_key]["expense_total"] += tx["amount"]

    sorted_groups = sorted(groups.values(), key=lambda g: g["date"], reverse=True)
    for g in sorted_groups:
        g["income_total"] = round(g["income_total"], 2)
        g["expense_total"] = round(g["expense_total"], 2)
    return sorted_groups


def get_top_expense(transactions: list) -> Optional[dict]:
    expenses = [t for t in transactions if t["type"] == "expense"]
    if not expenses:
        return None
    return max(expenses, key=lambda t: t["amount"])


def get_last_balance(transactions: list) -> Optional[dict]:
    """Берёт самую новую транзакцию с balance."""
    with_balance = [t for t in transactions if t.get("balance") is not None]
    if not with_balance:
        return None
    # Сортируем по дате убыванию, берём первую
    with_balance.sort(key=lambda t: parse_transaction_datetime(t) or datetime.min, reverse=True)
    tx = with_balance[0]
    return {
        "balance": tx["balance"],
        "balance_currency": tx.get("balance_currency", "UZS"),
        "card_last4": tx.get("card_last4"),
        "date": tx.get("date"),
    }


async def load_transactions_from_humo(
    x_session_token: str,
    limit: int = 500,
    offset_id: int = 0,
) -> dict:
    """
    Подключается к Telegram, читает сообщения из @HUMOcardbot,
    парсит и нормализует транзакции.
    """
    client = None
    try:
        client = create_client(x_session_token)
        await connect_client(client)
        await ensure_authorized(client)

        try:
            entity = await client.get_entity("@HUMOcardbot")
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"HUMO bot не найден: {e}")

        messages = await client.get_messages(
            entity,
            limit=limit,
            offset_id=offset_id if offset_id > 0 else 0,
        )

        transactions = []
        last_message_id = None

        for msg in messages:
            if msg.out:
                continue
            if msg.sender_id and msg.sender_id != entity.id:
                continue
            if not msg.text:
                continue

            text = msg.text

            if "история платежей" in text.lower():
                continue
            if text.count("➖") + text.count("➕") > 1:
                continue

            has_amount = "➕" in text or "➖" in text
            has_card = "💳" in text
            has_time = bool(re.search(r"\d{2}:\d{2}", text))

            if not (has_amount and has_card and has_time):
                continue

            tx = parse_humo_message(text, msg.id)
            if tx:
                tx_dict = tx.dict()
                tx_dict = normalize_transaction(tx_dict)
                transactions.append(tx_dict)
                last_message_id = msg.id

        return {
            "transactions": transactions,
            "has_more": len(messages) == limit,
            "next_offset_id": last_message_id,
        }

    finally:
        if client:
            await disconnect_client(client)