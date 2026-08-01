FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python-зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всего проекта
COPY . .

# Создание директорий для данных
RUN mkdir -p /app/data /app/logs

# Переменная окружения для базы данных
ENV DB_PATH=/app/data/tg_key_bot.db
ENV PYTHONUNBUFFERED=1

# Порт для Django админки
EXPOSE 8123

# Команда запуска
CMD ["python", "run_bot_with_django.py"]
