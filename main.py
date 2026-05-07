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
import json
import os
import base64
import io
from datetime import datetime
from typing import Optional
import asyncio

app = FastAPI(title="Finance App API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === КОНФИГИ — заполни своими данными из my.telegram.org ===
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Хранилище сессий пользователей (в проде используй БД)
user_sessions: dict[str, str] = {}

# ============================================================
# МОДЕЛИ
# ============================================================

class PhoneRequest(BaseModel):
    phone: str  # "+998901234567"

class CodeRequest(BaseModel):
    phone: str
    phone_code_hash: str
    code: str
    password: Optional[str] = None

class Transaction(BaseModel):
    id: str
    date: str
    amount: float
    currency: str
    type: str        # "expense" или "income"
    description: str
    raw_text: str

# Временное хранилище для phone_code_hash
pending_logins: dict[str, dict] = {}

# ============================================================
# ПАРСЕР СООБЩЕНИЙ HUMO БОТА
# ============================================================

def parse_humo_message(text: str) -> Optional[Transaction]:
    """
    Парсит сообщение от @HUMOcardbot и извлекает транзакцию.
    
    Примеры сообщений от бота:
    "Списание: 50 000 UZS\nMagазин Korzinka\n12.05.2026 14:30"
    "Зачисление: 1 000 000 UZS\nПеревод от Алишер\n12.05.2026 10:00"
    """
    
    text_lower = text.lower()
    
    # Определяем тип транзакции
    is_expense = any(word in text_lower for word in [
        'списание', 'расход', 'оплата', 'покупка', 'снятие', 'debit'
    ])
    is_income = any(word in text_lower for word in [
        'зачисление', 'пополнение', 'перевод получен', 'доход', 'credit'
    ])
    
    if not (is_expense or is_income):
        return None
    
    # Извлекаем сумму (например: 50 000 UZS или 50000 UZS)
    amount_match = re.search(
        r'(\d[\d\s]*\d|\d+)\s*(UZS|uzs|сум|sum)',
        text,
        re.IGNORECASE
    )
    if not amount_match:
        return None
    
    # Убираем пробелы из числа: "50 000" -> 50000
    amount_str = amount_match.group(1).replace(' ', '').replace('\xa0', '')
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    
    # Извлекаем дату
    date_match = re.search(
        r'(\d{2}[.\-/]\d{2}[.\-/]\d{4})',
        text
    )
    date_str = date_match.group(1) if date_match else datetime.now().strftime('%d.%m.%Y')
    
    # Описание — берём строку после суммы или первую значимую строку
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    description = lines[0] if lines else "Транзакция"
    # Убираем слова-маркеры из описания
    for marker in ['Списание:', 'Зачисление:', 'Расход:', 'Пополнение:']:
        description = description.replace(marker, '').strip()
    
    import hashlib
    tx_id = hashlib.md5(text.encode()).hexdigest()[:12]
    
    return Transaction(
        id=tx_id,
        date=date_str,
        amount=amount,
        currency="UZS",
        type="expense" if is_expense else "income",
        description=description,
        raw_text=text[:200]
    )

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root():
    return {"status": "ok", "message": "Finance App API работает"}


@app.post("/auth/send-code")
async def send_code(req: PhoneRequest):
    """Шаг 1: Отправляем код на номер телефона"""
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        result = await client.send_code_request(req.phone)
        
        # Сохраняем client и hash для следующего шага
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
                raise HTTPException(
                    status_code=401,
                    detail="TWO_STEP_PASSWORD_REQUIRED"
                )

            await client.sign_in(password=req.password)

        # Сохраняем финальную сессию
        session_string = client.session.save()
        user_sessions[req.phone] = session_string

        # Получаем инфо о пользователе
        me = await client.get_me()

        # Скачиваем фото профиля в память
        photo_base64 = None
        try:
            photo_bytes = await client.download_profile_photo(me, file=bytes)
            if photo_bytes:
                photo_base64 = base64.b64encode(photo_bytes).decode("utf-8")
        except Exception:
            photo_base64 = None

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

def analyze_humo_connection_state(messages) -> dict:
    """
    Проверяет только важное:
    1. Подключена ли карта HUMO
    2. Есть ли HUMO/SMS-информирование на этот номер
    3. Можно ли читать транзакции
    """

    ordered_messages = list(reversed(messages))

    card_connected = False
    humo_account_for_phone = None
    sms_code_waiting = False
    sms_code_invalid = False
    phone_requested = False
    no_card_or_account = False

    matched_signals = []

    for msg in ordered_messages:
        text = msg.text or ""
        text_lower = text.lower()

        # 1. Признаки подключенной карты / успешного состояния
        card_patterns = [
            r"\*{4}\s?\d{4}",
            r"\b(8600|9860)\s?\*{2,}",
            r"\b(8600|9860)\s?\d{2}\*+",
        ]

        card_words = [
            "баланс",
            "остаток",
            "доступно",
            "мои карты",
            "карты",
            "карта humo",
            "uzs",
            "сум",
            "пополнение",
            "списание",
            "оплата",
            "перевод",
        ]

        if any(re.search(pattern, text_lower) for pattern in card_patterns):
            card_connected = True
            humo_account_for_phone = True
            matched_signals.append("card_mask_detected")

        if any(word in text_lower for word in card_words):
            # Важно: не считаем приветственное описание бота как карту
            if not (
                "получать информацию по вашим humo картам" in text_lower
                or "управлять ими напрямую" in text_lower
                or "для пользования данным ботом" in text_lower
            ):
                card_connected = True
                humo_account_for_phone = True
                matched_signals.append("card_or_transaction_words_detected")

        # 2. Признаки, что карт/аккаунта нет
        no_account_words = [
            "на данный номер не зарегистрирован",
            "номер не зарегистрирован",
            "не зарегистрирован",
            "карта не найдена",
            "карты не найдены",
            "нет активных карт",
            "не найдено карт",
            "sms-информирования не подключена",
            "sms-информирование не подключено",
            "услуга sms-информирования не подключена",
            "по данному номеру не найден",
            "по данному номеру не найдены",
        ]

        if any(word in text_lower for word in no_account_words):
            no_card_or_account = True
            humo_account_for_phone = False
            matched_signals.append("no_card_or_account_detected")

        # 3. Промежуточные состояния, но только как reason
        if (
            "поделитесь своим номером" in text_lower
            or "номер должен совпадать" in text_lower
        ):
            phone_requested = True
            matched_signals.append("phone_requested")

        if (
            "sms-сообщение с кодом" in text_lower
            or "введите код" in text_lower
            or "введите 6-значный код" in text_lower
        ):
            sms_code_waiting = True
            matched_signals.append("sms_code_waiting")

        if "неверный код подтверждения" in text_lower:
            sms_code_invalid = True
            matched_signals.append("sms_code_invalid")

    # Приоритет 1: если карта найдена, всё хорошо
    if card_connected:
        return {
            "is_registered": True,
            "is_card_connected": True,
            "has_humo_account_for_phone": True,
            "can_read_transactions": True,
            "status": "card_connected",
            "reason": "Карта HUMO найдена в сообщениях бота",
            "matched_signals": list(set(matched_signals)),
        }

    # Приоритет 2: если бот явно сказал, что карты/аккаунта нет
    if no_card_or_account:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "has_humo_account_for_phone": False,
            "can_read_transactions": False,
            "status": "no_card_or_account_for_phone",
            "reason": "HUMO bot сообщил, что карта или SMS-информирование не найдены для этого номера",
            "matched_signals": list(set(matched_signals)),
        }

    # Приоритет 3: код неверный
    if sms_code_invalid:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "has_humo_account_for_phone": None,
            "can_read_transactions": False,
            "status": "sms_code_invalid",
            "reason": "Пользователь ввёл неверный SMS-код, карта ещё не подключена",
            "matched_signals": list(set(matched_signals)),
        }

    # Приоритет 4: ждёт код
    if sms_code_waiting:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "has_humo_account_for_phone": None,
            "can_read_transactions": False,
            "status": "sms_code_waiting",
            "reason": "HUMO bot ждёт SMS-код, карта ещё не подключена",
            "matched_signals": list(set(matched_signals)),
        }

    # Приоритет 5: просит номер
    if phone_requested:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "has_humo_account_for_phone": None,
            "can_read_transactions": False,
            "status": "phone_required",
            "reason": "HUMO bot просит отправить номер телефона",
            "matched_signals": list(set(matched_signals)),
        }

    return {
        "is_registered": False,
        "is_card_connected": False,
        "has_humo_account_for_phone": None,
        "can_read_transactions": False,
        "status": "not_connected_or_unknown",
        "reason": "Карта HUMO не найдена в последних сообщениях бота",
        "matched_signals": list(set(matched_signals)),
    }

@app.get("/transactions")
async def get_transactions(
    x_session_token: str = Header(...),
    limit: int = 100
):
    """Получаем транзакции из @HUMOcardbot"""
    try:
        client = TelegramClient(
            StringSession(x_session_token),
            API_ID,
            API_HASH
        )
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            raise HTTPException(status_code=401, detail="Сессия истекла")
        
        # Получаем сообщения из @HUMOcardbot
        messages = await client.get_messages('@HUMOcardbot', limit=limit)
        
        transactions = []
        for msg in messages:
            if msg.text:
                tx = parse_humo_message(msg.text)
                if tx:
                    transactions.append(tx.dict())
        
        await client.disconnect()
        
        return {
            "success": True,
            "count": len(transactions),
            "transactions": transactions
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/check-bot")
async def check_bot(x_session_token: str = Header(...)):
    """Проверяем Telegram-сессию, наличие HUMO bot и этап регистрации внутри HUMO bot"""
    client = None

    try:
        client = TelegramClient(
            StringSession(x_session_token),
            API_ID,
            API_HASH
        )
        await client.connect()

        if not await client.is_user_authorized():
            raise HTTPException(
                status_code=401,
                detail="SESSION_EXPIRED"
            )

        try:
            entity = await client.get_entity("@HUMOcardbot")
            messages = await client.get_messages(entity, limit=100)

            has_messages = len(messages) > 0
            humo_auth = analyze_humo_auth_state(messages)

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
                "humo_auth": humo_auth
            }

        except Exception as e:
            return {
                "success": True,
                "authorized": True,
                "has_bot": False,
                "has_messages": False,
                "humo_auth": {
                    "is_registered": False,
                    "is_card_connected": False,
                    "status": "bot_not_found",
                    "current_step": 0,
                    "next_action": "Откройте @HUMOcardbot и нажмите /start",
                    "flags": {}
                },
                "message": "HUMO bot не найден в чатах пользователя"
            }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if client:
            await client.disconnect()