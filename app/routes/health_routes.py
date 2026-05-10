import os
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def root():
    return {"status": "ok", "message": "Finance App API работает"}


@router.get("/debug-env")
async def debug_env():
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    return {
        "api_id_exists": bool(os.getenv("TELEGRAM_API_ID")),
        "api_id_value": os.getenv("TELEGRAM_API_ID"),
        "api_hash_exists": bool(api_hash),
        "api_hash_length": len(api_hash),
        "api_hash_preview": api_hash[:4] + "***" if api_hash else None,
    }