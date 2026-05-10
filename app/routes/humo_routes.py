from fastapi import APIRouter, Header, HTTPException
from app.services.telegram_client_service import create_client, connect_client, disconnect_client, ensure_authorized
from app.services.humo_state_service import analyze_humo_connection_state

router = APIRouter()


@router.get("/check-bot")
async def check_bot(x_session_token: str = Header(...)):
    client = None
    try:
        client = create_client(x_session_token)
        await connect_client(client)
        await ensure_authorized(client)

        try:
            entity = await client.get_entity("@HUMOcardbot")
        except Exception as e:
            return {
                "success": True, "authorized": True, "has_bot": False, "has_messages": False,
                "humo": {
                    "has_bot_started": False, "is_registered": False,
                    "is_card_connected": False, "can_read_transactions": False,
                    "status": "bot_not_found", "reason": str(e), "matched_signals": []
                },
                "message": "HUMO bot не найден в чатах пользователя",
            }

        messages = await client.get_messages(entity, limit=100)
        humo = analyze_humo_connection_state(messages)

        return {
            "success": True,
            "authorized": True,
            "has_bot": True,
            "has_messages": len(messages) > 0,
            "bot": {
                "id": entity.id,
                "username": getattr(entity, "username", None),
                "title": getattr(entity, "first_name", None),
            },
            "humo": humo,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if client:
            await disconnect_client(client)