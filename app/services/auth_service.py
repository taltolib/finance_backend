from telethon.errors import SessionPasswordNeededError
from fastapi import HTTPException
from typing import Optional
from app.services.telegram_client_service import (
    create_client, connect_client, disconnect_client,
    ensure_authorized, get_me
)

# TODO: заменить in-memory pending_logins на Redis/database для production
pending_logins: dict[str, dict] = {}


async def send_telegram_code(phone: str) -> dict:
    client = create_client()
    await connect_client(client)
    try:
        result = await client.send_code_request(phone)
        session_string = client.session.save()
        pending_logins[phone] = {
            "session": session_string,
            "phone_code_hash": result.phone_code_hash,
        }
        return {
            "success": True,
            "phone_code_hash": result.phone_code_hash,
            "message": "Код отправлен в Telegram",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await disconnect_client(client)


async def verify_telegram_code(
    phone: str,
    phone_code_hash: str,
    code: str,
    password: Optional[str] = None,
) -> dict:
    pending = pending_logins.get(phone)
    if not pending:
        raise HTTPException(status_code=400, detail="Сначала запросите код")

    client = create_client(pending["session"])
    await connect_client(client)

    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise HTTPException(status_code=401, detail="TWO_STEP_PASSWORD_REQUIRED")
            await client.sign_in(password=password)

        session_token = client.session.save()
        user = await get_me(client)
        user["phone"] = phone

        del pending_logins[phone]

        return {
            "success": True,
            "session_token": session_token,
            "user": user,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await disconnect_client(client)


async def get_current_user(session_token: str) -> dict:
    client = create_client(session_token)
    await connect_client(client)
    try:
        await ensure_authorized(client)
        user = await get_me(client)
        return {"success": True, "user": user}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await disconnect_client(client)


async def logout_user(session_token: str) -> dict:
    client = create_client(session_token)
    try:
        await connect_client(client)
        if await client.is_user_authorized():
            await client.log_out()
        return {"success": True, "message": "Вы вышли из аккаунта"}
    except Exception:
        return {"success": True, "message": "Сессия завершена"}
    finally:
        await disconnect_client(client)