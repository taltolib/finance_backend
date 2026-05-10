"""
Finance App Backend
Читает сообщения из @HUMOcardbot через Telegram Userbot
и отдаёт транзакции Flutter приложению через REST API
"""

# ============================================================
# БЛОК 1: IMPORTS
# ============================================================

from telethon.errors import SessionPasswordNeededError
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession
import re
import os
import base64
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, List
from calendar import monthrange

app = FastAPI(title="Finance App API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

user_sessions: dict[str, str] = {}
pending_logins: dict[str, dict] = {}

# ============================================================
# БЛОК 2: КОНСТАНТЫ
# ============================================================

RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

RU_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

RU_WEEKDAYS = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}

RU_WEEKDAYS_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"
}

CATEGORY_RULES = [
    ("transfer",  "Переводы",  ["p2p", "hu2hu", "card2card", "перевод", "зачисление перевода", "payme", "click"]),
    ("food",      "Еда",       ["evos", "kfc", "oqtepa", "lavash", "cafe", "restaurant", "ресторан", "кафе", "burger", "pizza", "пицца"]),
    ("market",    "Магазины",  ["korzinka", "makro", "havas", "market", "supermarket", "магазин", "супермаркет", "store"]),
    ("taxi",      "Такси",     ["yandex", "taxi", "mytaxi", "такси", "yandexgo", "uber"]),
    ("mobile",    "Связь",     ["beeline", "uzmobile", "ucell", "mobiuz", "paynet", "телефон"]),
    ("cash",      "Наличные",  ["atm", "банкомат", "снятие", "cash"]),
    ("shopping",  "Покупки",   ["uzum", "olx", "wildberries", "aliexpress", "shop"]),
]
KANBAN_CATEGORIES = [
    {"id": "uncategorized", "title": "Неразобранные"},
    {"id": "food", "title": "Еда"},
    {"id": "transport", "title": "Транспорт"},
    {"id": "market", "title": "Магазины"},
    {"id": "transfer", "title": "Переводы"},
    {"id": "subscription", "title": "Подписки"},
    {"id": "cash", "title": "Наличные"},
    {"id": "shopping", "title": "Покупки"},
    {"id": "mobile", "title": "Связь"},
    {"id": "other", "title": "Другое"},
    {"id": "ignored", "title": "Игнорировать"},
]
# ============================================================
# МОДЕЛИ
# ============================================================

class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    phone: str
    phone_code_hash: str
    code: str
    password: Optional[str] = None

class Transaction(BaseModel):
    id: str
    telegram_message_id: Optional[int] = None
    date: str
    time: Optional[str] = None
    datetime: Optional[str] = None
    amount: float
    currency: str
    type: str
    title: str
    description: str
    merchant: Optional[str] = None
    card_name: Optional[str] = None
    card_last4: Optional[str] = None
    balance: Optional[float] = None
    balance_currency: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    category_title: Optional[str] = None
    raw_text: str

# ============================================================
# ПАРСЕР СООБЩЕНИЙ HUMO БОТА
# ============================================================

def parse_uzs_amount(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace("\xa0", "").replace("'", "")
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts[-1]) == 3:
            cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_humo_message(text: str, message_id: Optional[int] = None) -> Optional[Transaction]:
    if not text:
        return None

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text_lower = text.lower()

    transaction_keywords = [
        "пополнение", "списание", "оплата", "перевод",
        "зачисление", "снятие", "uzs", "сум", "humocard"
    ]
    if not any(word in text_lower for word in transaction_keywords):
        return None

    income_words = ["пополнение", "зачисление", "перевод получен", "credit", "➕", "🎉"]
    expense_words = ["списание", "оплата", "покупка", "снятие", "debit", "➖", "💸"]

    is_income = any(word in text_lower or word in text for word in income_words)
    is_expense = any(word in text_lower or word in text for word in expense_words)

    if not is_income and not is_expense:
        return None

    tx_type = "income" if is_income else "expense"

    first_line = lines[0] if lines else ""
    icon = None
    title = first_line

    icon_match = re.match(r"^([^\w\s]+)\s*(.+)$", first_line)
    if icon_match:
        icon = icon_match.group(1).strip()
        title = icon_match.group(2).strip()

    amount = None
    currency = "UZS"
    amount_line = None

    for line in lines:
        if "➕" in line or "➖" in line:
            amount_line = line
            break

    if not amount_line:
        amount_line = text

    amount_match = re.search(
        r"([+-]?\d[\d\s'.,]*)\s*(UZS|uzs|сум|sum)",
        amount_line,
        re.IGNORECASE
    )
    if not amount_match:
        return None

    amount = parse_uzs_amount(amount_match.group(1))
    if amount is None:
        return None

    currency = amount_match.group(2).upper()
    if currency in ["СУМ", "SUM"]:
        currency = "UZS"

    merchant = None
    for line in lines:
        if "📍" in line:
            merchant = line.replace("📍", "").strip()
            break

    card_name = None
    card_last4 = None
    for line in lines:
        if "💳" in line or "humocard" in line.lower():
            card_line = line.replace("💳", "").strip()
            card_match = re.search(r"([A-Za-zА-Яа-я0-9 ]+)\s+\*+(\d{4})", card_line)
            if card_match:
                card_name = card_match.group(1).strip()
                card_last4 = card_match.group(2).strip()
            else:
                card_name = card_line
            break

    time_str = None
    date_str = None
    datetime_str = None

    datetime_match = re.search(
        r"(\d{2}:\d{2})\s+(\d{2}[.\-/]\d{2}[.\-/]\d{4})",
        text
    )
    if datetime_match:
        time_str = datetime_match.group(1)
        date_str = datetime_match.group(2)
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            datetime_str = dt.isoformat()
        except ValueError:
            datetime_str = None
    else:
        date_match = re.search(r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})", text)
        date_str = date_match.group(1) if date_match else datetime.now().strftime("%d.%m.%Y")

    balance = None
    balance_currency = None
    for line in lines:
        if "💰" in line:
            balance_match = re.search(
                r"([+-]?\d[\d\s'.,]*)\s*(UZS|uzs|сум|sum)",
                line,
                re.IGNORECASE
            )
            if balance_match:
                balance = parse_uzs_amount(balance_match.group(1))
                balance_currency = balance_match.group(2).upper()
                if balance_currency in ["СУМ", "SUM"]:
                    balance_currency = "UZS"
            break

    description = merchant or title or "Транзакция"
    tx_source = f"{message_id or ''}:{text}"
    tx_id = hashlib.md5(tx_source.encode()).hexdigest()[:12]

    return Transaction(
        id=tx_id,
        telegram_message_id=message_id,
        date=date_str,
        time=time_str,
        datetime=datetime_str,
        amount=amount,
        currency=currency,
        type=tx_type,
        title=title,
        description=description,
        merchant=merchant,
        card_name=card_name,
        card_last4=card_last4,
        balance=balance,
        balance_currency=balance_currency,
        icon=icon,
        raw_text=text[:500]
    )

# ============================================================
# АНАЛИЗ СОСТОЯНИЯ HUMO БОТА
# ============================================================

def analyze_humo_connection_state(messages) -> dict:
    ordered_messages = list(reversed(messages))

    has_bot_started = False
    card_connected = False
    no_card_or_account = False
    sms_code_waiting = False
    sms_code_invalid = False
    phone_requested = False
    congratulations_found = False
    matched_signals = []

    for msg in ordered_messages:
        text = msg.text or ""
        text_lower = text.lower()

        if (
            (msg.out and "/start" in text_lower)
            or "tilni tanlang" in text_lower
            or "выберите язык" in text_lower
            or "добро пожаловать" in text_lower
            or "публичной оферты" in text_lower
        ):
            has_bot_started = True
            matched_signals.append("bot_started")

        if "поздравляем" in text_lower and "подключились" in text_lower and not msg.out:
            congratulations_found = True
            card_connected = True
            matched_signals.append("congratulations_detected")

        card_patterns = [
            r"\*{4}\s?\d{4}",
            r"\b(8600|9860)\s?\*{2,}",
            r"humocard\s+\*\d{4}",
            r"humocard\s+ipakyulibank\s+\*\d{4}",
            r"humocard\s+ao\s+anor\s+bank\s+\*\d{4}",
        ]
        if not msg.out and any(re.search(p, text_lower) for p in card_patterns):
            card_connected = True
            matched_signals.append("card_mask_detected")

        no_account_words = [
            "на данный номер не зарегистрирован",
            "номер не зарегистрирован",
            "карта не найдена",
            "карты не найдены",
            "нет активных карт",
            "sms-информирования не подключена",
            "sms-информирование не подключено",
            "услуга sms-информирования не подключена",
            "по данному номеру не найден",
        ]
        if not msg.out and any(word in text_lower for word in no_account_words):
            no_card_or_account = True
            matched_signals.append("no_card_or_account_detected")

        if not msg.out and (
            "поделитесь своим номером" in text_lower
            or "номер должен совпадать" in text_lower
        ):
            phone_requested = True
            matched_signals.append("phone_requested")

        if not msg.out and (
            "sms-сообщение с кодом" in text_lower
            or "введите код" in text_lower
            or "введите 6-значный код" in text_lower
        ):
            sms_code_waiting = True
            matched_signals.append("sms_code_waiting")

        if not msg.out and "неверный код подтверждения" in text_lower:
            sms_code_invalid = True
            matched_signals.append("sms_code_invalid")

    unique_signals = list(set(matched_signals))

    if no_card_or_account and not card_connected:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "no_card_or_account_for_phone", "reason": "HUMO bot сообщил что карта не найдена для этого номера", "matched_signals": unique_signals}
    if card_connected:
        return {"has_bot_started": True, "is_registered": True, "is_card_connected": True, "can_read_transactions": True, "status": "card_connected", "reason": "Поздравление от HUMO bot получено — карта подключена" if congratulations_found else "Карта HUMO найдена в сообщениях", "matched_signals": unique_signals}
    if sms_code_invalid:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "sms_code_invalid", "reason": "Неверный SMS-код", "matched_signals": unique_signals}
    if sms_code_waiting:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "sms_code_waiting", "reason": "HUMO bot ждёт SMS-код", "matched_signals": unique_signals}
    if phone_requested:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "phone_required", "reason": "HUMO bot просит номер телефона", "matched_signals": unique_signals}

    return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "started_not_registered" if has_bot_started else "not_started", "reason": "Бот запущен но карта не подключена" if has_bot_started else "Бот не запускался", "matched_signals": unique_signals}

# ============================================================
# БЛОК 3: HELPER FUNCTIONS
# ============================================================

def detect_category(tx: dict) -> tuple:
    search_text = " ".join(filter(None, [
        tx.get("merchant", ""),
        tx.get("title", ""),
        tx.get("description", ""),
        tx.get("raw_text", ""),
    ])).lower()

    for cat_id, cat_title, keywords in CATEGORY_RULES:
        if any(kw in search_text for kw in keywords):
            return cat_id, cat_title
    return "other", "Другое"


def parse_transaction_datetime(tx: dict) -> Optional[datetime]:
    """
    Надёжно превращает transaction date/time в datetime.

    Поддерживает:
    - tx["datetime"] = "2026-05-01T11:01:00"
    - tx["date"] = "01.05.2026" + tx["time"] = "11:01"
    - tx["date"] = "01-05-2026"
    - tx["date"] = "01/05/2026"

    Если time нет, используется 00:00.
    """
    if not tx:
        return None

    dt_value = tx.get("datetime")
    if dt_value:
        try:
            return datetime.fromisoformat(str(dt_value))
        except Exception:
            pass

    date_value = tx.get("date")
    if not date_value:
        return None

    time_value = tx.get("time") or "00:00"

    date_formats = [
        "%d.%m.%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ]

    for date_format in date_formats:
        try:
            return datetime.strptime(
                f"{date_value} {time_value}",
                f"{date_format} %H:%M"
            )
        except Exception:
            continue

    for date_format in date_formats:
        try:
            return datetime.strptime(str(date_value), date_format)
        except Exception:
            continue

    return None

def normalize_transaction(tx: dict) -> dict:
    cat_id, cat_title = detect_category(tx)
    tx["category"] = cat_id
    tx["category_title"] = cat_title
    return tx


def get_month_range(month: str) -> tuple:
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    last_day = monthrange(year, mon)[1]
    end = date(year, mon, last_day)
    return start, end


def get_period_range(period: str) -> tuple:
    """
    Возвращает date range для analytics.

    day      -> сегодня
    week     -> текущая неделя с понедельника до сегодня
    month    -> текущий месяц с 1 числа до сегодня
    3months  -> последние 3 календарных месяца, включая текущий
    year     -> текущий год с 1 января до сегодня
    """
    today = date.today()

    if period == "day":
        return today, today

    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, today

    if period == "month":
        start = date(today.year, today.month, 1)
        return start, today

    if period == "3months":
        start_month = today.month - 2
        start_year = today.year

        while start_month <= 0:
            start_month += 12
            start_year -= 1

        start = date(start_year, start_month, 1)
        return start, today

    if period == "year":
        start = date(today.year, 1, 1)
        return start, today

    # fallback, если кто-то отправил мусор, хотя /analytics уже валидирует period
    start = today - timedelta(days=7)
    return start, today



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


def build_day_label(tx_date: date, selected_month: str) -> str:
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
    month = RU_MONTHS_GENITIVE[tx_date.month]
    return f"{weekday}, {day} {month}"


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


def build_top_categories(transactions: list) -> list:
    cat_totals: dict = {}
    for tx in transactions:
        if tx["type"] != "expense":
            continue
        cat_id = tx.get("category", "other")
        cat_title = tx.get("category_title", "Другое")
        if cat_id not in cat_totals:
            cat_totals[cat_id] = {"category": cat_id, "category_title": cat_title, "total": 0.0, "count": 0}
        cat_totals[cat_id]["total"] += tx["amount"]
        cat_totals[cat_id]["count"] += 1

    total_expense = sum(c["total"] for c in cat_totals.values())
    result = []
    for cat in sorted(cat_totals.values(), key=lambda c: c["total"], reverse=True):
        cat["total"] = round(cat["total"], 2)
        cat["percent"] = round(cat["total"] / total_expense * 100, 1) if total_expense > 0 else 0
        result.append(cat)
    return result[:6]


def get_top_expense(transactions: list) -> Optional[dict]:
    expenses = [t for t in transactions if t["type"] == "expense"]
    if not expenses:
        return None
    return max(expenses, key=lambda t: t["amount"])


def get_last_balance(transactions: list) -> Optional[dict]:
    """
    Берёт баланс из самой новой транзакции, где balance != None.
    Это баланс по последней операции, не гарантированно текущий банковский баланс.
    """
    sorted_transactions = sorted(
        transactions,
        key=lambda tx: parse_transaction_datetime(tx) or datetime.min,
        reverse=True
    )

    for tx in sorted_transactions:
        if tx.get("balance") is not None:
            return {
                "balance": tx.get("balance"),
                "balance_currency": tx.get("balance_currency") or tx.get("currency") or "UZS",
                "card_last4": tx.get("card_last4"),
                "date": tx.get("date"),
                "time": tx.get("time"),
                "datetime": tx.get("datetime"),
            }

    return None


def get_previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


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


def build_chart(transactions: list, period: str, start: date, end: date) -> list:
    """
    Строит chart для analytics.

    day:
      24 точки по часам.

    week:
      7 дней текущей недели, даже если end=today.

    month:
      все дни от start до end.

    3months:
      по месяцам от start до end.

    year:
      все 12 месяцев текущего года.
    """

    def add_amount(point: dict, tx: dict) -> None:
        amount = tx.get("amount", 0) or 0
        if tx.get("type") == "income":
            point["income"] += amount
        elif tx.get("type") == "expense":
            point["expense"] += amount

    if period == "day":
        chart = {
            h: {
                "label": f"{h:02d}:00",
                "date": start.isoformat(),
                "income": 0.0,
                "expense": 0.0,
            }
            for h in range(24)
        }

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue
            add_amount(chart[dt.hour], tx)

        return [
            {
                "label": item["label"],
                "date": item["date"],
                "income": round(item["income"], 2),
                "expense": round(item["expense"], 2),
            }
            for item in chart.values()
        ]

    if period == "week":
        week_start = start
        week_end = start + timedelta(days=6)

        chart = {}
        current = week_start

        while current <= week_end:
            key = current.isoformat()
            chart[key] = {
                "label": RU_WEEKDAYS_SHORT[current.weekday()],
                "date": key,
                "income": 0.0,
                "expense": 0.0,
            }
            current += timedelta(days=1)

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue

            key = dt.date().isoformat()
            if key in chart:
                add_amount(chart[key], tx)

        return [
            {
                "label": item["label"],
                "date": item["date"],
                "income": round(item["income"], 2),
                "expense": round(item["expense"], 2),
            }
            for item in chart.values()
        ]

    if period == "month":
        chart = {}
        current = start

        while current <= end:
            key = current.isoformat()
            chart[key] = {
                "label": str(current.day),
                "date": key,
                "income": 0.0,
                "expense": 0.0,
            }
            current += timedelta(days=1)

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue

            key = dt.date().isoformat()
            if key in chart:
                add_amount(chart[key], tx)

        return [
            {
                "label": item["label"],
                "date": item["date"],
                "income": round(item["income"], 2),
                "expense": round(item["expense"], 2),
            }
            for item in chart.values()
        ]

    if period == "3months":
        chart = {}
        current = date(start.year, start.month, 1)

        while current <= end:
            key = current.strftime("%Y-%m")
            chart[key] = {
                "label": RU_MONTHS[current.month],
                "date": key,
                "income": 0.0,
                "expense": 0.0,
            }

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue

            key = dt.strftime("%Y-%m")
            if key in chart:
                add_amount(chart[key], tx)

        return [
            {
                "label": item["label"],
                "date": item["date"],
                "income": round(item["income"], 2),
                "expense": round(item["expense"], 2),
            }
            for item in chart.values()
        ]

    if period == "year":
        chart = {}

        for month_num in range(1, 13):
            key = f"{start.year}-{month_num:02d}"
            chart[key] = {
                "label": RU_MONTHS[month_num],
                "date": key,
                "income": 0.0,
                "expense": 0.0,
            }

        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue

            key = dt.strftime("%Y-%m")
            if key in chart:
                add_amount(chart[key], tx)

        return [
            {
                "label": item["label"],
                "date": item["date"],
                "income": round(item["income"], 2),
                "expense": round(item["expense"], 2),
            }
            for item in chart.values()
        ]

    return []

# ============================================================
# БЛОК 4: LOAD TRANSACTIONS FROM HUMO
# ============================================================
# TODO Production:
# Сейчас /transactions, /dashboard и /analytics читают Telegram напрямую.
# Это нормально для MVP, но плохо для больших данных и старых месяцев.
# Позже нужно сделать:
# Telegram HUMO bot -> sync -> database -> dashboard/analytics.
# Иначе limit=500 может не покрыть старые месяцы.

async def load_transactions_from_humo(
    x_session_token: str,
    limit: int = 500,
    offset_id: int = 0
) -> dict:
    client = None
    try:
        client = TelegramClient(StringSession(x_session_token), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="SESSION_EXPIRED")

        entity = await client.get_entity("@HUMOcardbot")
        messages = await client.get_messages(
            entity,
            limit=limit,
            offset_id=offset_id if offset_id > 0 else 0
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
            try:
                await client.disconnect()
            except Exception:
                pass


# ============================================================
# ЭНДПОИНТЫ
# ============================================================

@app.get("/")
async def root():
    return {"status": "ok", "message": "Finance App API работает"}


@app.get("/debug-env")
async def debug_env():
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    return {
        "api_id_exists": bool(os.getenv("TELEGRAM_API_ID")),
        "api_id_value": os.getenv("TELEGRAM_API_ID"),
        "api_hash_exists": bool(api_hash),
        "api_hash_length": len(api_hash),
        "api_hash_preview": api_hash[:4] + "***" if api_hash else None
    }


@app.post("/auth/send-code")
async def send_code(req: PhoneRequest):
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        result = await client.send_code_request(req.phone)
        session_string = client.session.save()
        pending_logins[req.phone] = {
            "session": session_string,
            "phone_code_hash": result.phone_code_hash
        }
        await client.disconnect()
        return {"success": True, "phone_code_hash": result.phone_code_hash, "message": "Код отправлен в Telegram"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/verify-code")
async def verify_code(req: CodeRequest):
    pending = pending_logins.get(req.phone)
    if not pending:
        raise HTTPException(status_code=400, detail="Сначала запросите код")
    try:
        client = TelegramClient(StringSession(pending["session"]), API_ID, API_HASH)
        await client.connect()
        try:
            await client.sign_in(phone=req.phone, code=req.code, phone_code_hash=req.phone_code_hash)
        except SessionPasswordNeededError:
            if not req.password:
                await client.disconnect()
                raise HTTPException(status_code=401, detail="TWO_STEP_PASSWORD_REQUIRED")
            await client.sign_in(password=req.password)

        session_string = client.session.save()
        user_sessions[req.phone] = session_string
        me = await client.get_me()

        photo_base64 = None
        try:
            photo_bytes = await client.download_profile_photo(me, file=bytes)
            if photo_bytes:
                photo_base64 = base64.b64encode(photo_bytes).decode("utf-8")
        except Exception:
            pass

        await client.disconnect()
        del pending_logins[req.phone]

        return {
            "success": True,
            "session_token": session_string,
            "user": {
                "id": me.id,
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "username": me.username,
                "phone": req.phone,
                "photo_base64": photo_base64,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/auth/me")
async def get_me(x_session_token: str = Header(...)):
    client = None
    try:
        client = TelegramClient(StringSession(x_session_token), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
        me = await client.get_me()
        photo_base64 = None
        try:
            photo_bytes = await client.download_profile_photo(me, file=bytes)
            if photo_bytes:
                photo_base64 = base64.b64encode(photo_bytes).decode("utf-8")
        except Exception:
            pass
        return {
            "success": True,
            "user": {
                "id": me.id,
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "username": me.username,
                "photo_base64": photo_base64,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if client:
            await client.disconnect()


@app.post("/auth/logout")
async def logout(x_session_token: str = Header(...)):
    client = None
    try:
        client = TelegramClient(StringSession(x_session_token), API_ID, API_HASH)
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
        return {"success": True, "message": "Вы вышли из аккаунта"}
    except Exception:
        return {"success": True, "message": "Сессия завершена"}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


@app.get("/check-bot")
async def check_bot(x_session_token: str = Header(...)):
    client = None
    try:
        client = TelegramClient(StringSession(x_session_token), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="SESSION_EXPIRED")
        try:
            entity = await client.get_entity("@HUMOcardbot")
        except Exception as e:
            return {
                "success": True, "authorized": True, "has_bot": False, "has_messages": False,
                "humo": {"has_bot_started": False, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "bot_not_found", "reason": str(e), "matched_signals": []},
                "message": "HUMO bot не найден в чатах пользователя"
            }
        messages = await client.get_messages(entity, limit=100)
        humo = analyze_humo_connection_state(messages)
        return {
            "success": True, "authorized": True, "has_bot": True, "has_messages": len(messages) > 0,
            "bot": {"id": entity.id, "username": getattr(entity, "username", None), "title": getattr(entity, "first_name", None)},
            "humo": humo
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if client:
            await client.disconnect()


# ============================================================
# БЛОК 5: ОБНОВЛЁННЫЙ /transactions
# ============================================================

@app.get("/transactions")
async def get_transactions(
    x_session_token: str = Header(...),
    limit: int = 50,
    offset_id: int = 0
):
    try:
        result = await load_transactions_from_humo(x_session_token, limit=limit, offset_id=offset_id)
        transactions = result["transactions"]
        income_total = sum(t["amount"] for t in transactions if t["type"] == "income")
        expense_total = sum(t["amount"] for t in transactions if t["type"] == "expense")
        return {
            "success": True,
            "count": len(transactions),
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "currency": "UZS",
            "has_more": result["has_more"],
            "next_offset_id": result["next_offset_id"],
            "transactions": transactions,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# БЛОК 6: GET /dashboard
# ============================================================

@app.get("/dashboard")
async def get_dashboard(
    x_session_token: str = Header(...),
    month: Optional[str] = Query(None, description="Формат YYYY-MM, например 2026-05")
):
    try:
        today = date.today()
        current_month = today.strftime("%Y-%m")

        if month is None:
            month = current_month

        try:
            year, mon = int(month[:4]), int(month[5:7])
            selected_month_date = date(year, mon, 1)
            current_month_date = date(today.year, today.month, 1)
        except Exception:
            raise HTTPException(status_code=400, detail="Неверный формат month. Используйте YYYY-MM")

        if selected_month_date > current_month_date:
            raise HTTPException(status_code=400, detail="Нельзя запрашивать будущий месяц")

        month_title = f"{RU_MONTHS[mon]} {year}"
        start_date, end_date = get_month_range(month)

        result = await load_transactions_from_humo(x_session_token, limit=500)
        all_transactions = result["transactions"]

        month_transactions = filter_transactions_by_date_range(all_transactions, start_date, end_date)
        month_transactions.sort(key=lambda t: parse_transaction_datetime(t) or datetime.min, reverse=True)

        current_summary = calculate_summary(month_transactions)

        previous_month = get_previous_month(month)
        prev_start, prev_end = get_month_range(previous_month)
        prev_transactions = filter_transactions_by_date_range(all_transactions, prev_start, prev_end)
        previous_summary = calculate_summary(prev_transactions) if prev_transactions else None

        insight = build_month_insight(current_summary, previous_summary)
        transaction_groups = group_transactions_by_day(month_transactions, month)
        top_categories = build_top_categories(month_transactions)
        top_expense = get_top_expense(month_transactions)
        last_balance = get_last_balance(month_transactions)

        return {
            "success": True,
            "screen": "dashboard",
            "month": month,
            "month_title": month_title,
            "can_go_next_month": selected_month_date < current_month_date,
            "can_go_previous_month": True,
            "currency": "UZS",
            "summary": current_summary,
            "top_categories": top_categories,
            "top_expense": top_expense,
            "last_balance": last_balance,
            "insight": insight,
            "transaction_groups": transaction_groups,
            "has_more": result["has_more"],
            "next_offset_id": result["next_offset_id"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# БЛОК 7: GET /analytics
# ============================================================

@app.get("/analytics")
async def get_analytics(
    x_session_token: str = Header(...),
    period: str = Query("week", description="day | week | month | 3months | year")
):
    try:
        valid_periods = ["day", "week", "month", "3months", "year"]
        if period not in valid_periods:
            raise HTTPException(status_code=400, detail=f"Неверный period. Допустимые: {', '.join(valid_periods)}")

        start_date, end_date = get_period_range(period)

        limit_map = {"day": 100, "week": 200, "month": 300, "3months": 500, "year": 500}
        load_limit = limit_map.get(period, 300)

        result = await load_transactions_from_humo(x_session_token, limit=load_limit)
        all_transactions = result["transactions"]

        period_transactions = filter_transactions_by_date_range(all_transactions, start_date, end_date)
        period_transactions.sort(key=lambda t: parse_transaction_datetime(t) or datetime.min, reverse=True)

        summary = calculate_summary(period_transactions)
        chart = build_chart(period_transactions, period, start_date, end_date)
        categories = build_top_categories(period_transactions)
        top_expense = get_top_expense(period_transactions)
        last_balance = get_last_balance(period_transactions)

        current_month = date.today().strftime("%Y-%m")
        transaction_groups = group_transactions_by_day(period_transactions, current_month)

        return {
            "success": True,
            "screen": "analytics",
            "period": period,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "currency": "UZS",
            "summary": summary,
            "chart": chart,
            "categories": categories,
            "top_expense": top_expense,
            "last_balance": last_balance,
            "transaction_groups": transaction_groups,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# БЛОК 8: GET /kanban/categories
# ============================================================

@app.get("/kanban/categories")
async def get_kanban_categories():
    """
    Категории для будущего экрана 'Разбор расходов'.

    Сейчас backend только отдаёт список базовых категорий.
    Overrides/rules/notes будут отдельным этапом, когда появится database.
    """
    return {
        "success": True,
        "categories": KANBAN_CATEGORIES,
    }