from pydantic import BaseModel
from typing import Optional

class Transaction(BaseModel):
    id: str
    telegram_message_id: Optional[int] = None
    date: str
    time: Optional[str] = None
    datetime: Optional[str] = None
    amount: float
    currency: str
    type: str                    # "income" | "expense"
    title: str
    description: str
    merchant: Optional[str] = None
    card_name: Optional[str] = None
    card_last4: Optional[str] = None
    balance: Optional[float] = None
    balance_currency: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    category_title: Optional[str] = None
    raw_text: str