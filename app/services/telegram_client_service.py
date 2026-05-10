from telethon import TelegramClient
from telethon.sessions import StringSession
from fastapi import HTTPException
from typing import Optional
from app.core.config import API_ID, API_HASH


def create_client(session_token: Optional[str] = None) -> TelegramClient:
    session = StringSession(session_token) if session_token else StringSession()
    return TelegramClient(session, API_ID, API_HASH)


async def connect_client(client: TelegramClient) -> None:
    await client.connect()


async def disconnect_client(client: TelegramClient) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


async def ensure_authorized(client: TelegramClient) -> None:
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="SESSION_EXPIRED")


async def get_authorized_client(session_token: str) -> TelegramClient:
    """Создаёт, подключает и проверяет авторизацию клиента."""
    client = create_client(session_token)
    await connect_client(client)
    await ensure_authorized(client)
    return client


async def get_humo_entity(client: TelegramClient):
    """Возвращает entity @HUMOcardbot или бросает HTTPException."""
    try:
        return await client.get_entity("@HUMOcardbot")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"HUMO bot не найден: {e}")


async def get_me(client: TelegramClient) -> dict:
    """Возвращает данные текущего пользователя."""
    import base64
    me = await client.get_me()

    photo_base64 = None
    try:
        photo_bytes = await client.download_profile_photo(me, file=bytes)
        if photo_bytes:
            photo_base64 = base64.b64encode(photo_bytes).decode("utf-8")
    except Exception:
        pass

    return {
        "id": me.id,
        "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "username": me.username,
        "photo_base64": photo_base64,
    }