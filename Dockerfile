# ── Базовый образ ──────────────────────────────────────────
FROM python:3.11-slim

# ── Системные пакеты ───────────────────────────────────────
# gcc / libffi нужны для сборки cryptg (быстрое шифрование Telethon)
# curl нужен для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

# ── Рабочая директория ─────────────────────────────────────
WORKDIR /app

# ── Зависимости (отдельным слоем — кешируются при сборке) ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Код приложения ─────────────────────────────────────────
COPY main.py .

# ── Переменные окружения (значения задаются снаружи) ───────
ENV TELEGRAM_API_ID=""
ENV TELEGRAM_API_HASH=""
ENV PORT=8000

# ── Порт ───────────────────────────────────────────────────
EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# ── Запуск ─────────────────────────────────────────────────
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
