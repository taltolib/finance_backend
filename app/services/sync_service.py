"""
sync_service.py — TODO scaffold

Сейчас /dashboard и /analytics читают Telegram напрямую при каждом запросе.
Это MVP подход — работает, но медленно и не покрывает старую историю при limit=500.

Позже нужно добавить:
  Telegram → sync → database → dashboard/analytics

Этот сервис будет отвечать за:
  1. Периодическую синхронизацию новых транзакций из Telegram в БД
  2. Сохранение транзакций с привязкой к пользователю
  3. Получение транзакций из БД для dashboard/analytics (быстро, без Telegram)
"""

from typing import Optional


async def sync_transactions_for_user(session_token: str) -> dict:
    """
    TODO: подключиться к Telegram, загрузить новые транзакции
    и сохранить в database с привязкой к пользователю.
    """
    raise NotImplementedError("sync_transactions_for_user: database not implemented yet")


async def save_new_transactions(transactions: list, user_id: int) -> None:
    """
    TODO: сохранить транзакции в database.
    Использовать upsert по transaction.id чтобы не дублировать.
    """
    raise NotImplementedError("save_new_transactions: database not implemented yet")


async def get_transactions_from_database(
    user_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list:
    """
    TODO: получить транзакции из database по user_id и диапазону дат.
    Намного быстрее чем читать из Telegram каждый раз.
    """
    raise NotImplementedError("get_transactions_from_database: database not implemented yet")