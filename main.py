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
def analyze_humo_auth_state(messages) -> dict:
    """
    Анализирует историю сообщений @HUMOcardbot и определяет,
    прошёл ли пользователь регистрацию внутри HUMO bot.
    """

    flags = {
        "start_clicked": False,
        "language_selected": False,
        "agreement_seen": False,
        "agreement_accepted": False,
        "phone_requested": False,
        "phone_sent": False,
        "sms_code_requested": False,
        "sms_code_invalid": False,
        "registration_success": False,
        "card_detected": False,
    }

    last_invalid_code_index = -1
    last_sms_request_index = -1
    last_user_code_index = -1
    last_success_index = -1
    last_card_index = -1

    # get_messages возвращает от новых к старым, нам удобнее от старых к новым
    ordered_messages = list(reversed(messages))

    for index, msg in enumerate(ordered_messages):
        text = msg.text or ""
        text_lower = text.lower()

        # 1. Пользователь нажал /start
        if msg.out and "/start" in text_lower:
            flags["start_clicked"] = True

        # 2. Выбор языка
        if "tilni tanlang" in text_lower or "выберите язык" in text_lower:
            flags["start_clicked"] = True

        if msg.out and (
            "русский" in text_lower
            or "ўзбек" in text_lower
            or "o'zbek" in text_lower
            or "uzbek" in text_lower
            or "🇷🇺" in text
            or "🇺🇿" in text
        ):
            flags["language_selected"] = True

        # 3. Соглашение / оферта
        if (
            "публичной оферты" in text_lower
            or "соглашаетесь" in text_lower
            or "ответственность за доступ" in text_lower
        ):
            flags["agreement_seen"] = True

        if msg.out and (
            "согласен" in text_lower
            or "✅" in text
        ):
            flags["agreement_accepted"] = True

        # 4. Запрос номера
        if (
            "поделитесь своим номером" in text_lower
            or "номер должен совпадать" in text_lower
            or "sms-информирования по карте humo" in text_lower
        ):
            flags["phone_requested"] = True

        # Пользователь отправил контакт или номер
        if msg.out:
            has_phone_in_text = bool(re.search(r"\+998\s?\d{2}\s?\d{3}\s?\d{2}\s?\d{2}", text))
            has_contact = getattr(msg, "contact", None) is not None

            if has_phone_in_text or has_contact:
                flags["phone_sent"] = True

        # 5. SMS-код отправлен
        if (
            "sms-сообщение с кодом" in text_lower
            or "введите код" in text_lower
            or "введите 6-значный код" in text_lower
        ):
            flags["sms_code_requested"] = True
            last_sms_request_index = index

        # Пользователь отправил код
        if msg.out and re.fullmatch(r"\d{4,8}", text.strip()):
            last_user_code_index = index

        # Неверный код
        if "неверный код подтверждения" in text_lower:
            flags["sms_code_invalid"] = True
            last_invalid_code_index = index

        # Успешная регистрация
        if (
            "успешно" in text_lower
            or "активирован" in text_lower
            or "регистрация завершена" in text_lower
            or "добро пожаловать" in text_lower and "humocardbot" not in text_lower
        ):
            flags["registration_success"] = True
            last_success_index = index

        # Признаки подключенной карты
        if (
            "баланс" in text_lower
            or "мои карты" in text_lower
            or "карта" in text_lower and "humo" in text_lower
            or re.search(r"\b(8600|9860)\s?\*{2,}", text_lower)
            or re.search(r"\*{4}\s?\d{4}", text_lower)
        ):
            flags["card_detected"] = True
            last_card_index = index

    # Логика определения финального статуса

    if flags["card_detected"]:
        return {
            "is_registered": True,
            "is_card_connected": True,
            "status": "registered_card_connected",
            "current_step": 6,
            "next_action": None,
            "flags": flags,
        }

    if flags["registration_success"]:
        return {
            "is_registered": True,
            "is_card_connected": False,
            "status": "registered_no_card",
            "current_step": 6,
            "next_action": "Проверьте, подключена ли карта HUMO к SMS-информированию",
            "flags": flags,
        }

    if last_invalid_code_index > last_user_code_index or flags["sms_code_invalid"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "sms_code_invalid",
            "current_step": 5,
            "next_action": "Введите правильный SMS-код или запросите новый код в HUMO bot",
            "flags": flags,
        }

    if flags["sms_code_requested"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "sms_code_waiting",
            "current_step": 5,
            "next_action": "Введите SMS-код подтверждения в HUMO bot",
            "flags": flags,
        }

    if flags["phone_requested"] and not flags["phone_sent"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "phone_waiting",
            "current_step": 4,
            "next_action": "Отправьте номер телефона в HUMO bot",
            "flags": flags,
        }

    if flags["agreement_seen"] and not flags["agreement_accepted"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "agreement_waiting",
            "current_step": 3,
            "next_action": "Примите соглашение в HUMO bot",
            "flags": flags,
        }

    if flags["start_clicked"] and not flags["language_selected"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "language_waiting",
            "current_step": 2,
            "next_action": "Выберите язык в HUMO bot",
            "flags": flags,
        }

    if not flags["start_clicked"]:
        return {
            "is_registered": False,
            "is_card_connected": False,
            "status": "not_started",
            "current_step": 1,
            "next_action": "Нажмите /start в HUMO bot",
            "flags": flags,
        }

    return {
        "is_registered": False,
        "is_card_connected": False,
        "status": "unknown",
        "current_step": None,
        "next_action": "Не удалось точно определить этап регистрации",
        "flags": flags,
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