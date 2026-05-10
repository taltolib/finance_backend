import re
import hashlib
from datetime import datetime
from typing import Optional
from app.models.transaction import Transaction
from app.utils.amount_utils import parse_uzs_amount


def parse_humo_message(text: str, message_id: Optional[int] = None) -> Optional[Transaction]:
    if not text:
        return None

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text_lower = text.lower()

    transaction_keywords = [
        "пополнение", "списание", "оплата", "перевод",
        "зачисление", "снятие", "uzs", "сум", "humocard"
    ]
    if not any(word in text_lower for word in transaction_keywords):
        return None

    income_words = ["пополнение", "зачисление", "перевод получен", "credit", "➕", "🎉"]
    expense_words = ["списание", "оплата", "покупка", "снятие", "debit", "➖", "💸"]

    is_income = any(word in text_lower or word in text for word in income_words)
    is_expense = any(word in text_lower or word in text for word in expense_words)

    if not is_income and not is_expense:
        return None

    tx_type = "income" if is_income else "expense"

    first_line = lines[0] if lines else ""
    icon = None
    title = first_line

    icon_match = re.match(r"^([^\w\s]+)\s*(.+)$", first_line)
    if icon_match:
        icon = icon_match.group(1).strip()
        title = icon_match.group(2).strip()

    amount = None
    currency = "UZS"
    amount_line = None

    for line in lines:
        if "➕" in line or "➖" in line:
            amount_line = line
            break
    if not amount_line:
        amount_line = text

    amount_match = re.search(
        r"([+-]?\d[\d\s'.,]*)\s*(UZS|uzs|сум|sum)",
        amount_line,
        re.IGNORECASE
    )
    if not amount_match:
        return None

    amount = parse_uzs_amount(amount_match.group(1))
    if amount is None:
        return None

    currency = amount_match.group(2).upper()
    if currency in ["СУМ", "SUM"]:
        currency = "UZS"

    merchant = None
    for line in lines:
        if "📍" in line:
            merchant = line.replace("📍", "").strip()
            break

    card_name = None
    card_last4 = None
    for line in lines:
        if "💳" in line or "humocard" in line.lower():
            card_line = line.replace("💳", "").strip()
            card_match = re.search(r"([A-Za-zА-Яа-я0-9 ]+)\s+\*+(\d{4})", card_line)
            if card_match:
                card_name = card_match.group(1).strip()
                card_last4 = card_match.group(2).strip()
            else:
                card_name = card_line
            break

    time_str = None
    date_str = None
    datetime_str = None

    datetime_match = re.search(r"(\d{2}:\d{2})\s+(\d{2}[.\-/]\d{2}[.\-/]\d{4})", text)
    if datetime_match:
        time_str = datetime_match.group(1)
        date_str = datetime_match.group(2)
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            datetime_str = dt.isoformat()
        except ValueError:
            datetime_str = None
    else:
        date_match = re.search(r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})", text)
        date_str = date_match.group(1) if date_match else datetime.now().strftime("%d.%m.%Y")

    balance = None
    balance_currency = None
    for line in lines:
        if "💰" in line:
            balance_match = re.search(
                r"([+-]?\d[\d\s'.,]*)\s*(UZS|uzs|сум|sum)",
                line,
                re.IGNORECASE
            )
            if balance_match:
                balance = parse_uzs_amount(balance_match.group(1))
                balance_currency = balance_match.group(2).upper()
                if balance_currency in ["СУМ", "SUM"]:
                    balance_currency = "UZS"
            break

    description = merchant or title or "Транзакция"
    tx_source = f"{message_id or ''}:{text}"
    tx_id = hashlib.md5(tx_source.encode()).hexdigest()[:12]

    return Transaction(
        id=tx_id,
        telegram_message_id=message_id,
        date=date_str,
        time=time_str,
        datetime=datetime_str,
        amount=amount,
        currency=currency,
        type=tx_type,
        title=title,
        description=description,
        merchant=merchant,
        card_name=card_name,
        card_last4=card_last4,
        balance=balance,
        balance_currency=balance_currency,
        icon=icon,
        raw_text=text[:500],
    )