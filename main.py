"""
Finance App Backend
Читает сообщения из @HUMOcardbot через Telegram Userbot
и отдаёт транзакции Flutter приложению через REST API
"""

from telethon.errors import SessionPasswordNeededError
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.sessions import StringSession
import re
import os
import base64
import hashlib
from datetime import datetime
from typing import Optional

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
    raw_text: str

# ============================================================
# ПАРСЕР СООБЩЕНИЙ HUMO БОТА
# ============================================================

def parse_uzs_amount(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace("\xa0", "")
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
        r"([+-]?\d[\d\s.,]*)\s*(UZS|uzs|сум|sum)",
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
                r"([+-]?\d[\d\s.,]*)\s*(UZS|uzs|сум|sum)",
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

        if (
            "поздравляем" in text_lower
            and "подключились" in text_lower
            and not msg.out
        ):
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
        return {
            "has_bot_started": has_bot_started,
            "is_registered": False,
            "is_card_connected": False,
            "can_read_transactions": False,
            "status": "no_card_or_account_for_phone",
            "reason": "HUMO bot сообщил что карта не найдена для этого номера",
            "matched_signals": unique_signals,
        }

    if card_connected:
        return {
            "has_bot_started": True,
            "is_registered": True,
            "is_card_connected": True,
            "can_read_transactions": True,
            "status": "card_connected",
            "reason": "Поздравление от HUMO bot получено — карта подключена" if congratulations_found else "Карта HUMO найдена в сообщениях",
            "matched_signals": unique_signals,
        }

    if sms_code_invalid:
        return {
            "has_bot_started": has_bot_started,
            "is_registered": False,
            "is_card_connected": False,
            "can_read_transactions": False,
            "status": "sms_code_invalid",
            "reason": "Неверный SMS-код",
            "matched_signals": unique_signals,
        }

    if sms_code_waiting:
        return {
            "has_bot_started": has_bot_started,
            "is_registered": False,
            "is_card_connected": False,
            "can_read_transactions": False,
            "status": "sms_code_waiting",
            "reason": "HUMO bot ждёт SMS-код",
            "matched_signals": unique_signals,
        }

    if phone_requested:
        return {
            "has_bot_started": has_bot_started,
            "is_registered": False,
            "is_card_connected": False,
            "can_read_transactions": False,
            "status": "phone_required",
            "reason": "HUMO bot просит номер телефона",
            "matched_signals": unique_signals,
        }

    return {
        "has_bot_started": has_bot_started,
        "is_registered": False,
        "is_card_connected": False,
        "can_read_transactions": False,
        "status": "started_not_registered" if has_bot_started else "not_started",
        "reason": "Бот запущен но карта не подключена" if has_bot_started else "Бот не запускался",
        "matched_signals": unique_signals,
    }


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
    """Шаг 1: Отправляем код на номер телефона"""
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
        return {
            "success": True,
            "phone_code_hash": result.phone_code_hash,
            "message": "Код отправлен в Telegram"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/verify-code")
async def verify_code(req: CodeRequest):
    """Шаг 2: Подтверждаем код и получаем сессию"""
    pending = pending_logins.get(req.phone)
    if not pending:
        raise HTTPException(status_code=400, detail="Сначала запросите код")

    try:
        client = TelegramClient(
            StringSession(pending["session"]),
            API_ID,
            API_HASH
        )
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
    """При старте приложения — проверяем токен и отдаём данные юзера"""
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
    """Выход из аккаунта"""
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
    """Проверяем Telegram-сессию, наличие HUMO bot и подключена ли карта"""
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
                "success": True,
                "authorized": True,
                "has_bot": False,
                "has_messages": False,
                "humo": {
                    "has_bot_started": False,
                    "is_registered": False,
                    "is_card_connected": False,
                    "can_read_transactions": False,
                    "status": "bot_not_found",
                    "reason": str(e),
                    "matched_signals": []
                },
                "message": "HUMO bot не найден в чатах пользователя"
            }

        messages = await client.get_messages(entity, limit=100)
        has_messages = len(messages) > 0
        humo = analyze_humo_connection_state(messages)

        return {
            "success": True,
            "authorized": True,
            "has_bot": True,
            "has_messages": has_messages,
            "bot": {
                "id": entity.id,
                "username": getattr(entity, "username", None),
                "title": getattr(entity, "first_name", None)
            },
            "humo": humo
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if client:
            await client.disconnect()


@app.get("/transactions")
async def get_transactions(
    x_session_token: str = Header(...),
    limit: int = 50,
    offset_id: int = 0
):
    """Получаем транзакции из @HUMOcardbot с пагинацией"""
    client = None
    try:
        client = TelegramClient(StringSession(x_session_token), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Сессия истекла")

        entity = await client.get_entity("@HUMOcardbot")
        messages = await client.get_messages(
            entity,
            limit=limit,
            offset_id=offset_id
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

            # Пропускаем сводные сообщения "История платежей"
            if "история платежей" in text.lower():
                continue

            # Пропускаем если содержит несколько транзакций (много ➖/➕)
            if text.count("➖") + text.count("➕") > 1:
                continue

            # Одиночная транзакция должна содержать ➕/➖, 💳, и время ЧЧ:ММ
            has_amount = "➕" in text or "➖" in text
            has_card = "💳" in text
            has_time = bool(re.search(r"\d{2}:\d{2}", text))

            if not (has_amount and has_card and has_time):
                continue

            tx = parse_humo_message(text, msg.id)
            if tx:
                transactions.append(tx.dict())
                last_message_id = msg.id

        income_total = sum(t["amount"] for t in transactions if t["type"] == "income")
        expense_total = sum(t["amount"] for t in transactions if t["type"] == "expense")

        return {
            "success": True,
            "count": len(transactions),
            "income_total": income_total,
            "expense_total": expense_total,
            "currency": "UZS",
            "has_more": len(messages) == limit,
            "next_offset_id": last_message_id,
            "transactions": transactions
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if client:
            await client.disconnect()