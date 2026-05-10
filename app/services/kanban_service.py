"""
kanban_service.py — TODO scaffold

Будущий экран "Разбор расходов" (Kanban доска).

Логика:
- Каждый месяц = отдельная доска
- Колонка "Неразобранные" — автоматически заполняется расходами месяца
- Пользователь может создавать свои колонки (Еда, Такси, Подписки...)
- Карточки (транзакции) перетаскиваются между колонками
- При перетаскивании пересчитывается сумма и количество в колонках
- Можно установить заметку к транзакции
- Можно игнорировать транзакцию (она не будет влиять на аналитику)
- Можно создать правило: keyword → category (автоматически)

Требует database для хранения:
- overrides (transaction_id → category, note, ignored)
- custom columns (user_id, month, column_name)
- rules (user_id, keyword, category)
"""

from typing import Optional


async def get_board_for_month(session_token: str, month: str) -> dict:
    """
    TODO: вернуть доску за месяц:
    - все расходы месяца в колонке "Неразобранные"
    - плюс пользовательские колонки из database
    - применить overrides к транзакциям
    """
    raise NotImplementedError("Kanban board: database not implemented yet")


async def save_category_override(
    session_token: str,
    transaction_id: str,
    month: str,
    category: str,
    note: Optional[str] = None,
    ignored: bool = False,
) -> dict:
    """
    TODO: сохранить override категории транзакции в database.
    """
    raise NotImplementedError("Category override: database not implemented yet")


async def get_overrides(session_token: str, month: str) -> list:
    """
    TODO: получить все overrides пользователя за месяц из database.
    """
    raise NotImplementedError("Get overrides: database not implemented yet")


async def save_rule(session_token: str, keyword: str, category: str) -> dict:
    """
    TODO: сохранить правило категоризации в database.
    Например: keyword="EVOS" → category="food"
    """
    raise NotImplementedError("Save rule: database not implemented yet")


async def get_rules(session_token: str) -> list:
    """
    TODO: получить все правила пользователя из database.
    """
    raise NotImplementedError("Get rules: database not implemented yet")