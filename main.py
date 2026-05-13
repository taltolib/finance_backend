"""
Finance App Backend
Reads messages from @HUMOcardbot via Telegram Userbot
Returns transactions to Flutter app via REST API

v2: Added SQLite persistence, Kanban board, transaction sync
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
import sqlite3
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, List
from calendar import monthrange
from contextlib import contextmanager

app = FastAPI(title="Finance App API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# TODO: implement AES encryption using SESSION_ENCRYPTION_KEY
# SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "")
# For now, session is stored as-is but NEVER logged

DB_PATH = os.getenv("FINANCE_DB_PATH", "finance_app.db")

pending_logins: dict[str, dict] = {}  # in-memory OTP pending

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

KANBAN_SYSTEM_COLUMNS = [
    {"id": "uncategorized", "title": "Неразобранные", "is_system": True, "position": 0},
]

# ============================================================
# БЛОК 3: DATABASE
# ============================================================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER UNIQUE,
            phone TEXT,
            name TEXT,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            photo_base64 TEXT,
            session_token_encrypted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            telegram_message_id INTEGER,
            tx_datetime TEXT,
            tx_date TEXT,
            tx_time TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UZS',
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            title TEXT,
            description TEXT,
            merchant TEXT,
            card_name TEXT,
            card_last4 TEXT,
            balance REAL,
            balance_currency TEXT,
            icon TEXT,
            category_id TEXT DEFAULT 'uncategorized',
            category_title TEXT DEFAULT 'Неразобранные',
            raw_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_transactions_user_datetime ON transactions(user_id, tx_datetime);
        CREATE INDEX IF NOT EXISTS idx_transactions_user_month ON transactions(user_id, tx_date);
        CREATE INDEX IF NOT EXISTS idx_transactions_user_type ON transactions(user_id, type);

        CREATE TABLE IF NOT EXISTS kanban_boards (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','archived')),
            created_at TEXT NOT NULL,
            archived_at TEXT,
            UNIQUE(user_id, month)
        );

        CREATE TABLE IF NOT EXISTS kanban_columns (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            board_id TEXT NOT NULL,
            title TEXT NOT NULL,
            is_system INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(board_id) REFERENCES kanban_boards(id)
        );

        CREATE TABLE IF NOT EXISTS kanban_cards (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            board_id TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            column_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, board_id, transaction_id),
            FOREIGN KEY(board_id) REFERENCES kanban_boards(id),
            FOREIGN KEY(transaction_id) REFERENCES transactions(id),
            FOREIGN KEY(column_id) REFERENCES kanban_columns(id)
        );
        """)


@app.on_event("startup")
async def startup():
    init_db()


# ============================================================
# БЛОК 4: DB HELPERS — USERS
# ============================================================

def upsert_user(telegram_user_id: int, phone: str, name: str, first_name: str,
                last_name: str, username: str, photo_base64: Optional[str],
                session_token: str) -> int:
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE users SET phone=?, name=?, first_name=?, last_name=?, username=?,
                photo_base64=COALESCE(?, photo_base64),
                session_token_encrypted=?, updated_at=?
                WHERE telegram_user_id=?
            """, (phone, name, first_name, last_name, username, photo_base64,
                  session_token, now, telegram_user_id))
            return existing["id"]
        else:
            conn.execute("""
                INSERT INTO users (telegram_user_id, phone, name, first_name, last_name,
                username, photo_base64, session_token_encrypted, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (telegram_user_id, phone, name, first_name, last_name, username,
                  photo_base64, session_token, now, now))
            return conn.execute(
                "SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,)
            ).fetchone()["id"]


def get_user_by_session(session_token: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE session_token_encrypted=?", (session_token,)
        ).fetchone()
        return dict(row) if row else None


# ============================================================
# БЛОК 5: DB HELPERS — TRANSACTIONS
# ============================================================

def upsert_transaction(user_id: int, tx: dict) -> None:
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, category_id, category_title FROM transactions WHERE id=?", (tx["id"],)
        ).fetchone()

        if existing:
            # Don't overwrite manually-set category
            cat_id = existing["category_id"]
            cat_title = existing["category_title"]
            conn.execute("""
                UPDATE transactions SET
                    telegram_message_id=?, tx_datetime=?, tx_date=?, tx_time=?,
                    amount=?, currency=?, type=?, title=?, description=?,
                    merchant=?, card_name=?, card_last4=?, balance=?, balance_currency=?,
                    icon=?, raw_text=?, updated_at=?
                WHERE id=?
            """, (
                tx.get("telegram_message_id"), tx.get("datetime"), tx.get("date"), tx.get("time"),
                tx["amount"], tx["currency"], tx["type"], tx.get("title"), tx.get("description"),
                tx.get("merchant"), tx.get("card_name"), tx.get("card_last4"),
                tx.get("balance"), tx.get("balance_currency"), tx.get("icon"),
                tx.get("raw_text", "")[:500], now, tx["id"]
            ))
        else:
            cat_id = tx.get("category", "uncategorized")
            cat_title = tx.get("category_title", "Неразобранные")
            conn.execute("""
                INSERT INTO transactions (
                    id, user_id, telegram_message_id, tx_datetime, tx_date, tx_time,
                    amount, currency, type, title, description, merchant, card_name,
                    card_last4, balance, balance_currency, icon, category_id, category_title,
                    raw_text, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                tx["id"], user_id, tx.get("telegram_message_id"),
                tx.get("datetime"), tx.get("date"), tx.get("time"),
                tx["amount"], tx["currency"], tx["type"],
                tx.get("title"), tx.get("description"), tx.get("merchant"),
                tx.get("card_name"), tx.get("card_last4"),
                tx.get("balance"), tx.get("balance_currency"),
                tx.get("icon"), cat_id, cat_title,
                tx.get("raw_text", "")[:500],
                now, now
            ))


def db_tx_to_dict(row) -> dict:
    r = dict(row)
    return {
        "id": r["id"],
        "telegram_message_id": r.get("telegram_message_id"),
        "date": r.get("tx_date"),
        "time": r.get("tx_time"),
        "datetime": r.get("tx_datetime"),
        "amount": r["amount"],
        "currency": r["currency"],
        "type": r["type"],
        "title": r.get("title"),
        "description": r.get("description"),
        "merchant": r.get("merchant"),
        "card_name": r.get("card_name"),
        "card_last4": r.get("card_last4"),
        "balance": r.get("balance"),
        "balance_currency": r.get("balance_currency"),
        "icon": r.get("icon"),
        "category": r.get("category_id"),
        "category_title": r.get("category_title"),
        "raw_text": r.get("raw_text", ""),
    }


def get_user_transactions(user_id: int, start: Optional[date] = None,
                          end: Optional[date] = None) -> list:
    with get_db() as conn:
        if start and end:
            rows = conn.execute("""
                SELECT * FROM transactions
                WHERE user_id=? AND tx_date >= ? AND tx_date <= ?
                ORDER BY tx_datetime DESC, tx_date DESC
            """, (user_id, start.isoformat(), end.isoformat())).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM transactions WHERE user_id=?
                ORDER BY tx_datetime DESC, tx_date DESC
            """, (user_id,)).fetchall()
        return [db_tx_to_dict(r) for r in rows]


# ============================================================
# БЛОК 6: PYDANTIC МОДЕЛИ
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

class CreateColumnRequest(BaseModel):
    board_id: str
    title: str

class RenameColumnRequest(BaseModel):
    title: str

class MoveCardRequest(BaseModel):
    board_id: str
    transaction_id: str
    from_column_id: str
    to_column_id: str
    new_index: int = 0


# ============================================================
# БЛОК 7: ПАРСЕР СООБЩЕНИЙ HUMO БОТА
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
# БЛОК 8: АНАЛИЗ СОСТОЯНИЯ HUMO БОТА
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
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "no_card_or_account_for_phone", "reason": "HUMO bot сообщил что карта не найдена для этого номера", "matched_signals": unique_signals}
    if card_connected:
        return {"has_bot_started": True, "is_registered": True, "is_card_connected": True, "has_humo_account_for_phone": True, "can_read_transactions": True, "status": "card_connected", "reason": "Поздравление от HUMO bot получено — карта подключена" if congratulations_found else "Карта HUMO найдена в сообщениях", "matched_signals": unique_signals}
    if sms_code_invalid:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "sms_code_invalid", "reason": "Неверный SMS-код", "matched_signals": unique_signals}
    if sms_code_waiting:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "sms_code_waiting", "reason": "HUMO bot ждёт SMS-код", "matched_signals": unique_signals}
    if phone_requested:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "phone_required", "reason": "HUMO bot просит номер телефона", "matched_signals": unique_signals}

    return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "started_not_registered" if has_bot_started else "not_started", "reason": "Бот запущен но карта не подключена" if has_bot_started else "Бот не запускался", "matched_signals": unique_signals}


# ============================================================
# БЛОК 9: HELPER FUNCTIONS
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

    date_formats = ["%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]

    for date_format in date_formats:
        try:
            return datetime.strptime(f"{date_value} {time_value}", f"{date_format} %H:%M")
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
    def add_amount(point: dict, tx: dict) -> None:
        amount = tx.get("amount", 0) or 0
        if tx.get("type") == "income":
            point["income"] += amount
        elif tx.get("type") == "expense":
            point["expense"] += amount

    if period == "day":
        chart = {
            h: {"label": f"{h:02d}:00", "date": start.isoformat(), "income": 0.0, "expense": 0.0}
            for h in range(24)
        }
        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue
            add_amount(chart[dt.hour], tx)
        return [{"label": item["label"], "date": item["date"], "income": round(item["income"], 2), "expense": round(item["expense"], 2)} for item in chart.values()]

    if period == "week":
        week_end = start + timedelta(days=6)
        chart = {}
        current = start
        while current <= week_end:
            key = current.isoformat()
            chart[key] = {"label": RU_WEEKDAYS_SHORT[current.weekday()], "date": key, "income": 0.0, "expense": 0.0}
            current += timedelta(days=1)
        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue
            key = dt.date().isoformat()
            if key in chart:
                add_amount(chart[key], tx)
        return [{"label": item["label"], "date": item["date"], "income": round(item["income"], 2), "expense": round(item["expense"], 2)} for item in chart.values()]

    if period == "month":
        chart = {}
        current = start
        while current <= end:
            key = current.isoformat()
            chart[key] = {"label": str(current.day), "date": key, "income": 0.0, "expense": 0.0}
            current += timedelta(days=1)
        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue
            key = dt.date().isoformat()
            if key in chart:
                add_amount(chart[key], tx)
        return [{"label": item["label"], "date": item["date"], "income": round(item["income"], 2), "expense": round(item["expense"], 2)} for item in chart.values()]

    if period == "3months":
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
            if not dt:
                continue
            key = dt.strftime("%Y-%m")
            if key in chart:
                add_amount(chart[key], tx)
        return [{"label": item["label"], "date": item["date"], "income": round(item["income"], 2), "expense": round(item["expense"], 2)} for item in chart.values()]

    if period == "year":
        chart = {}
        for month_num in range(1, 13):
            key = f"{start.year}-{month_num:02d}"
            chart[key] = {"label": RU_MONTHS[month_num], "date": key, "income": 0.0, "expense": 0.0}
        for tx in transactions:
            dt = parse_transaction_datetime(tx)
            if not dt:
                continue
            key = dt.strftime("%Y-%m")
            if key in chart:
                add_amount(chart[key], tx)
        return [{"label": item["label"], "date": item["date"], "income": round(item["income"], 2), "expense": round(item["expense"], 2)} for item in chart.values()]

    return []


# ============================================================
# БЛОК 10: TELEGRAM — LOAD & SYNC
# ============================================================

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


async def sync_transactions_for_user(x_session_token: str, limit: int = 500) -> dict:
    """
    Pull fresh transactions from HUMO bot and upsert to DB.
    Returns sync stats.
    """
    user = get_user_by_session(x_session_token)
    if not user:
        raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

    result = await load_transactions_from_humo(x_session_token, limit=limit)
    transactions = result["transactions"]

    new_count = 0
    updated_count = 0

    for tx in transactions:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM transactions WHERE id=?", (tx["id"],)
            ).fetchone()
        if existing:
            updated_count += 1
        else:
            new_count += 1
        upsert_transaction(user["id"], tx)

    return {
        "success": True,
        "synced": len(transactions),
        "new": new_count,
        "updated": updated_count,
        "has_more": result["has_more"],
        "next_offset_id": result["next_offset_id"],
    }


# ============================================================
# БЛОК 11: KANBAN HELPERS
# ============================================================

def ensure_current_board(user_id: int, month: str) -> dict:
    """
    Archive older active boards for this user.
    Create current month board if missing.
    Ensure system uncategorized column.
    Copy custom columns from latest previous board if current board is new.
    Returns the current board row as dict.
    """
    now = datetime.utcnow().isoformat()
    year, mon = int(month[:4]), int(month[5:7])
    month_title = f"{RU_MONTHS[mon]} {year}"

    with get_db() as conn:
        # Archive any active boards that are not the current month
        conn.execute("""
            UPDATE kanban_boards SET status='archived', archived_at=?
            WHERE user_id=? AND status='active' AND month != ?
        """, (now, user_id, month))

        # Get or create current board
        board = conn.execute(
            "SELECT * FROM kanban_boards WHERE user_id=? AND month=?", (user_id, month)
        ).fetchone()

        is_new_board = board is None

        if is_new_board:
            board_id = month  # e.g. "2026-05"
            conn.execute("""
                INSERT INTO kanban_boards (id, user_id, month, title, status, created_at)
                VALUES (?,?,?,?,?,?)
            """, (board_id, user_id, month, month_title, "active", now))
            board = conn.execute(
                "SELECT * FROM kanban_boards WHERE id=?", (board_id,)
            ).fetchone()
        else:
            board_id = board["id"]
            # ensure status is active if it's the current month
            conn.execute(
                "UPDATE kanban_boards SET status='active' WHERE id=?", (board_id,)
            )

        # Ensure system uncategorized column exists
        uncategorized = conn.execute(
            "SELECT id FROM kanban_columns WHERE board_id=? AND is_system=1", (board_id,)
        ).fetchone()

        if not uncategorized:
            conn.execute("""
                INSERT INTO kanban_columns (id, user_id, board_id, title, is_system, position, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (f"{board_id}:uncategorized", user_id, board_id, "Неразобранные", 1, 0, now, now))

        # Copy custom columns from latest previous board if this is a new board
        if is_new_board:
            prev_board = conn.execute("""
                SELECT id FROM kanban_boards
                WHERE user_id=? AND month < ?
                ORDER BY month DESC LIMIT 1
            """, (user_id, month)).fetchone()

            if prev_board:
                prev_columns = conn.execute("""
                    SELECT * FROM kanban_columns
                    WHERE board_id=? AND is_system=0
                    ORDER BY position ASC
                """, (prev_board["id"],)).fetchall()

                for col in prev_columns:
                    new_col_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO kanban_columns (id, user_id, board_id, title, is_system, position, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (new_col_id, user_id, board_id, col["title"], 0, col["position"], now, now))

        return dict(board)


def ensure_kanban_cards_for_month(user_id: int, board_id: str, month: str) -> None:
    """
    Make sure all expense transactions for this month have a kanban_card.
    New cards land in the uncategorized system column.
    """
    now = datetime.utcnow().isoformat()
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    last_day = monthrange(year, mon)[1]
    end = date(year, mon, last_day)

    system_col_id = f"{board_id}:uncategorized"

    with get_db() as conn:
        # Get all expense transactions for the month not yet in kanban_cards
        expenses = conn.execute("""
            SELECT t.id FROM transactions t
            WHERE t.user_id=?
              AND t.type='expense'
              AND t.tx_date >= ? AND t.tx_date <= ?
              AND NOT EXISTS (
                SELECT 1 FROM kanban_cards k
                WHERE k.transaction_id = t.id AND k.board_id = ?
              )
        """, (user_id, start.isoformat(), end.isoformat(), board_id)).fetchall()

        for tx in expenses:
            card_id = str(uuid.uuid4())
            # Get current max position in uncategorized
            max_pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM kanban_cards WHERE column_id=?",
                (system_col_id,)
            ).fetchone()[0]
            conn.execute("""
                INSERT OR IGNORE INTO kanban_cards (id, user_id, board_id, transaction_id, column_id, position, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (card_id, user_id, board_id, tx["id"], system_col_id, max_pos + 1, now, now))


def get_board_with_columns(user_id: int, board_id: str) -> dict:
    with get_db() as conn:
        board = conn.execute(
            "SELECT * FROM kanban_boards WHERE id=? AND user_id=?", (board_id, user_id)
        ).fetchone()
        if not board:
            return None

        columns = conn.execute("""
            SELECT * FROM kanban_columns WHERE board_id=? AND user_id=?
            ORDER BY position ASC, is_system DESC
        """, (board_id, user_id)).fetchall()

        result_columns = []
        for col in columns:
            cards_rows = conn.execute("""
                SELECT k.*, t.amount, t.currency, t.tx_date, t.tx_time, t.merchant, t.title, t.description
                FROM kanban_cards k
                JOIN transactions t ON t.id = k.transaction_id
                WHERE k.column_id=? AND k.board_id=?
                ORDER BY k.position ASC
            """, (col["id"], board_id)).fetchall()

            cards = []
            expense_total = 0.0
            for card in cards_rows:
                expense_total += card["amount"] or 0
                cards.append({
                    "id": card["id"],
                    "transaction_id": card["transaction_id"],
                    "place": card["merchant"] or card["title"] or card["description"],
                    "amount": card["amount"],
                    "currency": card["currency"],
                    "date": card["tx_date"],
                    "time": card["tx_time"],
                    "merchant": card["merchant"],
                    "column_id": card["column_id"],
                })

            result_columns.append({
                "id": col["id"],
                "title": col["title"],
                "is_system": bool(col["is_system"]),
                "position": col["position"],
                "transactions_count": len(cards),
                "expense_total": round(expense_total, 2),
                "cards": cards,
            })

        total_expense = sum(c["expense_total"] for c in result_columns)
        total_cards = sum(c["transactions_count"] for c in result_columns)

        return {
            "id": board["id"],
            "month": board["month"],
            "title": board["title"],
            "status": board["status"],
            "columns": result_columns,
            "summary": {
                "expense_total": round(total_expense, 2),
                "transactions_count": total_cards,
                "columns_count": len(result_columns),
            }
        }


# ============================================================
# БЛОК 12: AUTH ENDPOINTS
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
    client = None
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()

        result = await client.send_code_request(req.phone)
        session_string = client.session.save()

        # NOTE: never log session_string, phone_code_hash
        pending_logins[req.phone] = {
            "session": session_string,
            "phone_code_hash": result.phone_code_hash
        }

        code_type = type(result.type).__name__ if getattr(result, "type", None) else None
        next_type = type(result.next_type).__name__ if getattr(result, "next_type", None) else None

        return {
            "success": True,
            "phone_code_hash": result.phone_code_hash,
            "message": "Код отправлен в Telegram",
            "debug": {
                "code_type": code_type,
                "next_type": next_type,
                "timeout": getattr(result, "timeout", None)
            }
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


@app.post("/auth/verify-code")
async def verify_code(req: CodeRequest):
    pending = pending_logins.get(req.phone)
    if not pending:
        raise HTTPException(status_code=400, detail="Сначала запросите код")

    client = None
    try:
        client = TelegramClient(StringSession(pending["session"]), API_ID, API_HASH)
        await client.connect()

        try:
            await client.sign_in(
                phone=req.phone,
                code=req.code,
                phone_code_hash=req.phone_code_hash
            )
        except SessionPasswordNeededError:
            if not req.password:
                await client.disconnect()
                # Return clean 200 with flag, NOT 401/500
                return {
                    "success": False,
                    "requires_password": True,
                    "message": "Введите пароль двухфакторной аутентификации"
                }
            await client.sign_in(password=req.password)

        session_token = client.session.save()
        me = await client.get_me()

        photo_base64 = None
        try:
            photo_bytes = await client.download_profile_photo(me, file=bytes)
            if photo_bytes:
                photo_base64 = base64.b64encode(photo_bytes).decode("utf-8")
        except Exception:
            pass

        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        user_db_id = upsert_user(
            telegram_user_id=me.id,
            phone=req.phone,
            name=name,
            first_name=me.first_name or "",
            last_name=me.last_name or "",
            username=me.username or "",
            photo_base64=photo_base64,
            session_token=session_token,
        )

        del pending_logins[req.phone]

        return {
            "success": True,
            "session_token": session_token,
            "user": {
                "id": me.id,
                "name": name,
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "username": me.username,
                "photo_base64": photo_base64,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


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

        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        upsert_user(
            telegram_user_id=me.id,
            phone="",
            name=name,
            first_name=me.first_name or "",
            last_name=me.last_name or "",
            username=me.username or "",
            photo_base64=photo_base64,
            session_token=x_session_token,
        )

        return {
            "success": True,
            "user": {
                "id": me.id,
                "name": name,
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


# ============================================================
# БЛОК 13: CHECK BOT
# ============================================================

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
                "humo": {"has_bot_started": False, "is_registered": False, "is_card_connected": False, "has_humo_account_for_phone": False, "can_read_transactions": False, "status": "bot_not_found", "reason": str(e), "matched_signals": []},
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
# БЛОК 14: TRANSACTIONS — SYNC & GET
# ============================================================

@app.post("/transactions/sync")
async def sync_transactions(
    x_session_token: str = Header(...),
    limit: int = 500
):
    try:
        result = await sync_transactions_for_user(x_session_token, limit=limit)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/transactions")
async def get_transactions(
    x_session_token: str = Header(...),
    limit: int = 50,
    offset_id: int = 0,
    use_db: bool = True,
):
    try:
        if use_db:
            user = get_user_by_session(x_session_token)
            if user:
                transactions = get_user_transactions(user["id"])
                # Paginate from DB
                if offset_id > 0:
                    # Find index of offset_id in message IDs and slice
                    msg_ids = [t.get("telegram_message_id") for t in transactions]
                    try:
                        idx = next(i for i, mid in enumerate(msg_ids) if mid and mid <= offset_id)
                        transactions = transactions[idx:idx + limit]
                    except StopIteration:
                        transactions = []
                else:
                    transactions = transactions[:limit]

                income_total = sum(t["amount"] for t in transactions if t["type"] == "income")
                expense_total = sum(t["amount"] for t in transactions if t["type"] == "expense")
                return {
                    "success": True,
                    "count": len(transactions),
                    "income_total": round(income_total, 2),
                    "expense_total": round(expense_total, 2),
                    "currency": "UZS",
                    "has_more": False,
                    "next_offset_id": None,
                    "transactions": transactions,
                }

        # Fallback: read live from Telegram
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
# БЛОК 15: DASHBOARD
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

        # Try to serve from DB first
        user = get_user_by_session(x_session_token)
        if user:
            all_transactions = get_user_transactions(user["id"])
        else:
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
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# БЛОК 16: ANALYTICS
# ============================================================

@app.get("/analytics")
async def get_analytics(
    x_session_token: str = Header(...),
    period: str = Query("day", description="day | week | month | 3months | year")
):
    try:
        valid_periods = ["day", "week", "month", "3months", "year"]
        if period not in valid_periods:
            raise HTTPException(status_code=400, detail=f"Неверный period. Допустимые: {', '.join(valid_periods)}")

        start_date, end_date = get_period_range(period)

        user = get_user_by_session(x_session_token)
        if user:
            all_transactions = get_user_transactions(user["id"])
        else:
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


# Backward-compat aliases
@app.get("/analytics/summary")
async def analytics_summary(
    x_session_token: str = Header(...),
    period: str = Query("day")
):
    data = await get_analytics(x_session_token=x_session_token, period=period)
    return {"success": True, "data": data["summary"]}


@app.get("/analytics/chart")
async def analytics_chart(
    x_session_token: str = Header(...),
    period: str = Query("day")
):
    data = await get_analytics(x_session_token=x_session_token, period=period)
    return {"success": True, "data": data["chart"]}


# ============================================================
# БЛОК 17: KANBAN ENDPOINTS
# ============================================================

@app.get("/kanban/categories")
async def get_kanban_categories():
    return {
        "success": True,
        "categories": [
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
        ],
    }


@app.get("/kanban/current")
async def get_kanban_current(
    x_session_token: str = Header(...),
    month: Optional[str] = Query(None, description="YYYY-MM, default: current month")
):
    try:
        today = date.today()
        if month is None:
            month = today.strftime("%Y-%m")

        user = get_user_by_session(x_session_token)
        if not user:
            # Try to authenticate via Telegram and get/create user
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND — сначала вызови /transactions/sync")

        user_id = user["id"]

        # Sync fresh transactions first (light sync)
        await sync_transactions_for_user(x_session_token, limit=300)

        # Ensure board exists with correct columns
        ensure_current_board(user_id, month)

        board_id = month
        ensure_kanban_cards_for_month(user_id, board_id, month)

        board = get_board_with_columns(user_id, board_id)
        if not board:
            raise HTTPException(status_code=404, detail="Доска не найдена")

        return {"success": True, "board": board}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/kanban/archived")
async def get_kanban_archived(x_session_token: str = Header(...)):
    try:
        user = get_user_by_session(x_session_token)
        if not user:
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

        with get_db() as conn:
            boards = conn.execute("""
                SELECT id, month, title, status, created_at, archived_at
                FROM kanban_boards
                WHERE user_id=? AND status='archived'
                ORDER BY month DESC
            """, (user["id"],)).fetchall()

        result = []
        for b in boards:
            bd = dict(b)
            # Get summary for archived board
            with get_db() as conn:
                col_count = conn.execute(
                    "SELECT COUNT(*) FROM kanban_columns WHERE board_id=?", (b["id"],)
                ).fetchone()[0]
                card_count = conn.execute(
                    "SELECT COUNT(*) FROM kanban_cards WHERE board_id=?", (b["id"],)
                ).fetchone()[0]
            bd["columns_count"] = col_count
            bd["cards_count"] = card_count
            result.append(bd)

        return {"success": True, "boards": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/kanban/columns")
async def create_kanban_column(
    req: CreateColumnRequest,
    x_session_token: str = Header(...)
):
    try:
        user = get_user_by_session(x_session_token)
        if not user:
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

        now = datetime.utcnow().isoformat()
        col_id = str(uuid.uuid4())

        with get_db() as conn:
            board = conn.execute(
                "SELECT * FROM kanban_boards WHERE id=? AND user_id=?",
                (req.board_id, user["id"])
            ).fetchone()
            if not board:
                raise HTTPException(status_code=404, detail="Доска не найдена")

            max_pos = conn.execute(
                "SELECT COALESCE(MAX(position), 0) FROM kanban_columns WHERE board_id=?",
                (req.board_id,)
            ).fetchone()[0]

            conn.execute("""
                INSERT INTO kanban_columns (id, user_id, board_id, title, is_system, position, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (col_id, user["id"], req.board_id, req.title, 0, max_pos + 1, now, now))

        return {
            "success": True,
            "column": {
                "id": col_id,
                "board_id": req.board_id,
                "title": req.title,
                "is_system": False,
                "position": max_pos + 1,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/kanban/columns/{column_id}")
async def rename_kanban_column(
    column_id: str,
    req: RenameColumnRequest,
    x_session_token: str = Header(...)
):
    try:
        user = get_user_by_session(x_session_token)
        if not user:
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

        now = datetime.utcnow().isoformat()
        with get_db() as conn:
            col = conn.execute(
                "SELECT * FROM kanban_columns WHERE id=? AND user_id=?",
                (column_id, user["id"])
            ).fetchone()
            if not col:
                raise HTTPException(status_code=404, detail="Колонка не найдена")
            if col["is_system"]:
                raise HTTPException(status_code=400, detail="Системную колонку нельзя изменить")
            conn.execute(
                "UPDATE kanban_columns SET title=?, updated_at=? WHERE id=?",
                (req.title, now, column_id)
            )

        return {"success": True, "column": {"id": column_id, "title": req.title}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/kanban/columns/{column_id}")
async def delete_kanban_column(
    column_id: str,
    x_session_token: str = Header(...)
):
    try:
        user = get_user_by_session(x_session_token)
        if not user:
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

        now = datetime.utcnow().isoformat()
        with get_db() as conn:
            col = conn.execute(
                "SELECT * FROM kanban_columns WHERE id=? AND user_id=?",
                (column_id, user["id"])
            ).fetchone()
            if not col:
                raise HTTPException(status_code=404, detail="Колонка не найдена")
            if col["is_system"]:
                raise HTTPException(status_code=400, detail="Системную колонку нельзя удалить")

            board_id = col["board_id"]
            system_col = conn.execute(
                "SELECT id FROM kanban_columns WHERE board_id=? AND is_system=1",
                (board_id,)
            ).fetchone()

            if system_col:
                # Move cards to uncategorized
                conn.execute("""
                    UPDATE kanban_cards SET column_id=?, updated_at=? WHERE column_id=?
                """, (system_col["id"], now, column_id))

            conn.execute("DELETE FROM kanban_columns WHERE id=?", (column_id,))

        return {"success": True, "message": "Колонка удалена, карточки перемещены в Неразобранные"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/kanban/cards/move")
async def move_kanban_card(
    req: MoveCardRequest,
    x_session_token: str = Header(...)
):
    try:
        user = get_user_by_session(x_session_token)
        if not user:
            raise HTTPException(status_code=401, detail="SESSION_NOT_FOUND")

        now = datetime.utcnow().isoformat()
        with get_db() as conn:
            # Verify column belongs to user
            to_col = conn.execute(
                "SELECT * FROM kanban_columns WHERE id=? AND user_id=?",
                (req.to_column_id, user["id"])
            ).fetchone()
            if not to_col:
                raise HTTPException(status_code=404, detail="Целевая колонка не найдена")

            # Get card
            card = conn.execute("""
                SELECT * FROM kanban_cards
                WHERE transaction_id=? AND board_id=? AND user_id=?
            """, (req.transaction_id, req.board_id, user["id"])).fetchone()
            if not card:
                raise HTTPException(status_code=404, detail="Карточка не найдена")

            # Shift existing cards in target column to make room
            conn.execute("""
                UPDATE kanban_cards SET position = position + 1, updated_at=?
                WHERE column_id=? AND board_id=? AND position >= ?
            """, (now, req.to_column_id, req.board_id, req.new_index))

            # Move card
            conn.execute("""
                UPDATE kanban_cards SET column_id=?, position=?, updated_at=?
                WHERE id=?
            """, (req.to_column_id, req.new_index, now, card["id"]))

            # Update transaction category to match column title
            conn.execute("""
                UPDATE transactions SET category_id=?, category_title=?, updated_at=?
                WHERE id=?
            """, (req.to_column_id, to_col["title"], now, req.transaction_id))

        return {
            "success": True,
            "message": "Карточка перемещена",
            "card": {
                "transaction_id": req.transaction_id,
                "column_id": req.to_column_id,
                "position": req.new_index,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))