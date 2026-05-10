from typing import Optional
from fastapi import APIRouter, Header, Query, HTTPException
from app.services import dashboard_service

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard(
    x_session_token: str = Header(...),
    month: Optional[str] = Query(None, description="Формат YYYY-MM, например 2026-05"),
):
    try:
        return await dashboard_service.build_dashboard(
            session_token=x_session_token,
            month=month,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))