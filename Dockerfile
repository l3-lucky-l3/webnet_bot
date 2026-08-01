FROM python:3.11-slim

WORKDIR /app

# Настройки прокси для сборки (apt и pip)
# Переменные в нижнем регистре тоже важны - apt их использует
ARG HTTP_PROXY=http://127.0.0.1:10809
ARG HTTPS_PROXY=http://127.0.0.1:10809
ARG http_proxy=http://127.0.0.1:10809
ARG https_proxy=http://127.0.0.1:10809

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV http_proxy=${http_proxy}
ENV https_proxy=${https_proxy}
ENV NO_PROXY="localhost,127.0.0.1,deb.debian.org,security.debian.org"
ENV no_proxy="localhost,127.0.0.1,deb.debian.org,security.debian.org"

# Устанавливаем только необходимые пакеты (без gcc и libpq-dev, так как используем SQLite)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python-зависимостей через прокси
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

# Переменные окружения для доступа внутри контейнера
ENV DJANGO_API_URL=http://127.0.0.1:8123

# Команда запуска
CMD ["python", "run_bot_with_django.py"]
