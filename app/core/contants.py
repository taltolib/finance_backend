RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

RU_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

RU_WEEKDAYS = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}

RU_WEEKDAYS_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"
}

# (category_id, category_title, keywords)
CATEGORY_RULES = [
    ("transfer",  "Переводы",  ["p2p", "hu2hu", "card2card", "перевод", "зачисление перевода", "payme", "click"]),
    ("food",      "Еда",       ["evos", "kfc", "oqtepa", "lavash", "cafe", "restaurant", "ресторан", "кафе", "burger", "pizza", "пицца"]),
    ("market",    "Магазины",  ["korzinka", "makro", "havas", "market", "supermarket", "магазин", "супермаркет", "store"]),
    ("taxi",      "Такси",     ["yandex", "taxi", "mytaxi", "такси", "yandexgo", "uber"]),
    ("mobile",    "Связь",     ["beeline", "uzmobile", "ucell", "mobiuz", "paynet", "телефон"]),
    ("cash",      "Наличные",  ["atm", "банкомат", "снятие", "cash"]),
    ("shopping",  "Покупки",   ["uzum", "olx", "wildberries", "aliexpress", "shop"]),
]

# Kanban категории (для экрана Разбор расходов)
KANBAN_CATEGORIES = [
    {"id": "uncategorized", "title": "Неразобранные"},
    {"id": "food",          "title": "Еда"},
    {"id": "transport",     "title": "Транспорт"},
    {"id": "market",        "title": "Магазины"},
    {"id": "transfer",      "title": "Переводы"},
    {"id": "subscription",  "title": "Подписки"},
    {"id": "cash",          "title": "Наличные"},
    {"id": "other",         "title": "Другое"},
    {"id": "ignored",       "title": "Игнорировать"},
]