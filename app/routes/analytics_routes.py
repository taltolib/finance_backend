from fastapi import APIRouter, Header, Query, HTTPException
from app.services import analytics_service

router = APIRouter()


@router.get("/analytics")
async def get_analytics(
    x_session_token: str = Header(...),
    period: str = Query("week", description="day | week | month | 3months | year"),
):
    try:
        return await analytics_service.build_analytics(
            session_token=x_session_token,
            period=period,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))