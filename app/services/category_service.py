from app.core.constants import CATEGORY_RULES


def detect_category(tx: dict) -> tuple:
    """Определяет категорию транзакции по merchant/title/description/raw_text."""
    search_text = " ".join(filter(None, [
        tx.get("merchant", ""),
        tx.get("title", ""),
        tx.get("description", ""),
        tx.get("raw_text", ""),
    ])).lower()

    for cat_id, cat_title, keywords in CATEGORY_RULES:
        if any(kw in search_text for kw in keywords):
            return cat_id, cat_title

    return "other", "Другое"


def build_top_categories(transactions: list) -> list:
    """Строит топ категорий по расходам."""
    cat_totals: dict = {}

    for tx in transactions:
        if tx["type"] != "expense":
            continue
        cat_id = tx.get("category", "other")
        cat_title = tx.get("category_title", "Другое")
        if cat_id not in cat_totals:
            cat_totals[cat_id] = {"category": cat_id, "category_title": cat_title, "total": 0.0, "count": 0}
        cat_totals[cat_id]["total"] += tx["amount"]
        cat_totals[cat_id]["count"] += 1

    total_expense = sum(c["total"] for c in cat_totals.values())
    result = []
    for cat in sorted(cat_totals.values(), key=lambda c: c["total"], reverse=True):
        cat["total"] = round(cat["total"], 2)
        cat["percent"] = round(cat["total"] / total_expense * 100, 1) if total_expense > 0 else 0
        result.append(cat)

    return result[:6]