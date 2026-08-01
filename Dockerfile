FROM python:3.11-slim

WORKDIR /app

# Игнорируем системные прокси внутри контейнера
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV NO_PROXY="deb.debian.org,security.debian.org"

# Устанавливаем только необходимые пакеты (без gcc и libpq-dev, так как используем SQLite)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
