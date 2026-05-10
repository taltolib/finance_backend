from typing import Optional
from fastapi import APIRouter, Header, Query, HTTPException
from pydantic import BaseModel
from app.core.constants import KANBAN_CATEGORIES

router = APIRouter()


# ── Модели запросов ─────────────────────────────────────────

class CategoryOverrideRequest(BaseModel):
    transaction_id: str
    month: str
    category: str
    note: Optional[str] = None
    ignored: bool = False


class RuleRequest(BaseModel):
    keyword: str
    category: str


# ── Endpoints ───────────────────────────────────────────────

@router.get("/categories")
async def get_kanban_categories():
    """Возвращает список всех доступных категорий для Kanban доски."""
    return {"success": True, "categories": KANBAN_CATEGORIES}


@router.post("/overrides")
async def save_override(
    req: CategoryOverrideRequest,
    x_session_token: str = Header(...),
):
    """
    TODO: сохранить override категории транзакции.
    Пока возвращает 501 Not Implemented.
    """
    raise HTTPException(status_code=501, detail="Kanban overrides: database not implemented yet")


@router.get("/overrides")
async def get_overrides(
    x_session_token: str = Header(...),
    month: str = Query(..., description="Формат YYYY-MM"),
):
    """
    TODO: вернуть все overrides пользователя за месяц.
    Пока возвращает 501 Not Implemented.
    """
    raise HTTPException(status_code=501, detail="Kanban overrides: database not implemented yet")


@router.post("/rules")
async def save_rule(
    req: RuleRequest,
    x_session_token: str = Header(...),
):
    """
    TODO: сохранить правило категоризации.
    Например: keyword="EVOS" → category="food"
    """
    raise HTTPException(status_code=501, detail="Kanban rules: database not implemented yet")


@router.get("/rules")
async def get_rules(x_session_token: str = Header(...)):
    """
    TODO: вернуть все правила пользователя.
    """
    raise HTTPException(status_code=501, detail="Kanban rules: database not implemented yet")