"""
Finance App Backend
Читает сообщения из @HUMOcardbot через Telegram Userbot
и отдаёт транзакции Flutter приложению через REST API
"""

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
        
        await client.sign_in(
            phone=req.phone,
            code=req.code,
            phone_code_hash=req.phone_code_hash
        )
        
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    """Проверяем, есть ли у пользователя @HUMOcardbot в чатах"""
    try:
        client = TelegramClient(
            StringSession(x_session_token),
            API_ID,
            API_HASH
        )
        await client.connect()
        
        try:
            entity = await client.get_entity('@HUMOcardbot')
            messages = await client.get_messages('@HUMOcardbot', limit=1)
            has_messages = len(messages) > 0
        except Exception:
            await client.disconnect()
            return {"has_bot": False, "has_messages": False}
        
        await client.disconnect()
        return {
            "has_bot": True,
            "has_messages": has_messages
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
