import re


def analyze_humo_connection_state(messages) -> dict:
    ordered_messages = list(reversed(messages))

    has_bot_started = False
    card_connected = False
    no_card_or_account = False
    sms_code_waiting = False
    sms_code_invalid = False
    phone_requested = False
    congratulations_found = False
    matched_signals = []

    card_patterns = [
        r"\*{4}\s?\d{4}",
        r"\b(8600|9860)\s?\*{2,}",
        r"humocard\s+\*\d{4}",
        r"humocard\s+ipakyulibank\s+\*\d{4}",
        r"humocard\s+ao\s+anor\s+bank\s+\*\d{4}",
    ]

    no_account_words = [
        "на данный номер не зарегистрирован",
        "номер не зарегистрирован",
        "карта не найдена",
        "карты не найдены",
        "нет активных карт",
        "sms-информирования не подключена",
        "sms-информирование не подключено",
        "услуга sms-информирования не подключена",
        "по данному номеру не найден",
    ]

    for msg in ordered_messages:
        text = msg.text or ""
        text_lower = text.lower()

        if (
            (msg.out and "/start" in text_lower)
            or "tilni tanlang" in text_lower
            or "выберите язык" in text_lower
            or "добро пожаловать" in text_lower
            or "публичной оферты" in text_lower
        ):
            has_bot_started = True
            matched_signals.append("bot_started")

        if "поздравляем" in text_lower and "подключились" in text_lower and not msg.out:
            congratulations_found = True
            card_connected = True
            matched_signals.append("congratulations_detected")

        if not msg.out and any(re.search(p, text_lower) for p in card_patterns):
            card_connected = True
            matched_signals.append("card_mask_detected")

        if not msg.out and any(word in text_lower for word in no_account_words):
            no_card_or_account = True
            matched_signals.append("no_card_or_account_detected")

        if not msg.out and (
            "поделитесь своим номером" in text_lower
            or "номер должен совпадать" in text_lower
        ):
            phone_requested = True
            matched_signals.append("phone_requested")

        if not msg.out and (
            "sms-сообщение с кодом" in text_lower
            or "введите код" in text_lower
            or "введите 6-значный код" in text_lower
        ):
            sms_code_waiting = True
            matched_signals.append("sms_code_waiting")

        if not msg.out and "неверный код подтверждения" in text_lower:
            sms_code_invalid = True
            matched_signals.append("sms_code_invalid")

    unique_signals = list(set(matched_signals))

    if no_card_or_account and not card_connected:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "no_card_or_account_for_phone", "reason": "HUMO bot сообщил что карта не найдена для этого номера", "matched_signals": unique_signals}
    if card_connected:
        return {"has_bot_started": True, "is_registered": True, "is_card_connected": True, "can_read_transactions": True, "status": "card_connected", "reason": "Поздравление от HUMO bot получено — карта подключена" if congratulations_found else "Карта HUMO найдена в сообщениях", "matched_signals": unique_signals}
    if sms_code_invalid:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "sms_code_invalid", "reason": "Неверный SMS-код", "matched_signals": unique_signals}
    if sms_code_waiting:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "sms_code_waiting", "reason": "HUMO bot ждёт SMS-код", "matched_signals": unique_signals}
    if phone_requested:
        return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "phone_required", "reason": "HUMO bot просит номер телефона", "matched_signals": unique_signals}

    return {"has_bot_started": has_bot_started, "is_registered": False, "is_card_connected": False, "can_read_transactions": False, "status": "started_not_registered" if has_bot_started else "not_started", "reason": "Бот запущен но карта не подключена" if has_bot_started else "Бот не запускался", "matched_signals": unique_signals}