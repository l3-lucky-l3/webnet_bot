FROM python:3.11-slim

WORKDIR /app

# Аргументы для прокси (используются только в dev сборке)
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG http_proxy=""
ARG https_proxy=""

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV http_proxy=${http_proxy}
ENV https_proxy=${https_proxy}
ENV NO_PROXY="localhost,127.0.0.1,deb.debian.org,security.debian.org"
ENV no_proxy="localhost,127.0.0.1,deb.debian.org,security.debian.org"

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python-зависимостей
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всего проекта
COPY . .

# Создание директорий для данных
RUN mkdir -p /app/data /app/logs

# Переменные окружения для базы данных
ENV DB_PATH=/app/data/tg_key_bot.db
ENV PYTHONUNBUFFERED=1

# Порты для Django админки и вебхуков
EXPOSE 8123 8024

# Переменные окружения для доступа внутри контейнера
ENV DJANGO_API_URL=http://127.0.0.1:8123
ENV WEBHOOK_HOST=0.0.0.0
ENV WEBHOOK_PORT=8024

# Команда запуска
CMD ["python", "run_bot_with_django.py"]
