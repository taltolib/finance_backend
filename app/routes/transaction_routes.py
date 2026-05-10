from fastapi import APIRouter, Header, HTTPException
from app.services.transaction_service import load_transactions_from_humo

router = APIRouter()


@router.get("/transactions")
async def get_transactions(
    x_session_token: str = Header(...),
    limit: int = 50,
    offset_id: int = 0,
):
    try:
        result = await load_transactions_from_humo(x_session_token, limit=limit, offset_id=offset_id)
        transactions = result["transactions"]
        income_total = sum(t["amount"] for t in transactions if t["type"] == "income")
        expense_total = sum(t["amount"] for t in transactions if t["type"] == "expense")
        return {
            "success": True,
            "count": len(transactions),
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "currency": "UZS",
            "has_more": result["has_more"],
            "next_offset_id": result["next_offset_id"],
            "transactions": transactions,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))