import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корневой директории проекта
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []

# Цены (в рублях) для Night VPN
PRICES = {
    "week": 99,
    "month": 390,
    "3months": 990,
    "6months": 1890,
    "year": 3900,
}

# Цены для Обычный VPN (Remnawave) - тип подписки "regular_*"
REGULAR_VPN_PRICES = {
    "day": 19,
    "month": 190,
    "3months": 509,
    "6months": 950,
    "year": 1760,
    "2years": 3150,
}

REGULAR_VPN_PROFIT = {
    "day": 5,
    "month": 25,
    "3months": 70,
    "6months": 130,
    "year": 240,
    "2years": 450,
}

# Цены для Обычный VPN (bypass Remnawave, 1 squad)
FAST_VPN_PRICES = {
    "week": 99,    "month": 150,
    "3months": 399,
    "6months": 749,
    "year": 1390,
}

# Remnawave API настройки для Обычного VPN
REMNAWAVE_BASE_URL = os.getenv("REMNAWAVE_BASE_URL", "").strip() if os.getenv("REMNAWAVE_BASE_URL") else None
REMNAWAVE_API_KEY = os.getenv("REMNAWAVE_API_KEY", "").strip() if os.getenv("REMNAWAVE_API_KEY") else None
REMNAWAVE_DEFAULT_SQUAD_UUID = os.getenv("REMNAWAVE_DEFAULT_SQUAD_UUID", "").strip() if os.getenv("REMNAWAVE_DEFAULT_SQUAD_UUID") else None

# Настройки изображений
DISABLE_PHOTOS = os.getenv("DISABLE_PHOTOS", "false").lower() in ("true", "1", "yes")

# Обязательный канал для подписки
REQUIRED_CHANNEL_ID = -1003474889644  # ID канала (более надежно, чем username)
REQUIRED_CHANNEL = "@webnetru"  # Username канала для ссылок (t.me/webbnetru)

# Видео file_id (получите через @RawDataBot после отправки видео боту)
VIDEO_FILE_ID = os.getenv("VIDEO_FILE_ID", "").strip() if os.getenv("VIDEO_FILE_ID") else None

# URL видео (альтернативный способ)
VIDEO_URL = os.getenv("VIDEO_URL", "").strip() if os.getenv("VIDEO_URL") else None

# Ссылка на пост с видео в канале (самый простой способ)
VIDEO_POST_URL = os.getenv("VIDEO_POST_URL", "").strip() if os.getenv("VIDEO_POST_URL") else None

# Операторы
OPERATORS = "МТС, Билайн, Tele2, Yota"

# ID группы для уведомлений поддержки (замените на ваш ID группы)
SUPPORT_GROUP_ID = os.getenv("SUPPORT_GROUP_ID")  # Установите ID группы в .env файле

# ЮKassa настройки (устарело, используется Platega)
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1164974")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "test_9tNtDsJFy4h-_hGjtuUCrH0Tw3bwvQWAFdx4BgdkPHc")
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL", "https://thoughtfully-active-manakin.cloudpub.ru/bot_management/webhook/yookassa/")

# Platega настройки
PLATEGA_MERCHANT_ID = os.getenv("PLATEGA_MERCHANT_ID", "")
PLATEGA_SECRET = os.getenv("PLATEGA_SECRET", "")
PLATEGA_WEBHOOK_URL = os.getenv("PLATEGA_WEBHOOK_URL", "https://thoughtfully-active-manakin.cloudpub.ru/bot_management/webhook/platega/")

# CryptoBot (Telegram) настройки
CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "")
CRYPTOBOT_WEBHOOK_URL = os.getenv("CRYPTOBOT_WEBHOOK_URL", "https://thoughtfully-active-manakin.cloudpub.ru/bot_management/webhook/cryptobot/")

# Antilopay настройки
ANTILOPAY_SECRET_ID = os.getenv("ANTILOPAY_SECRET_ID", "")
ANTILOPAY_PRIVATE_KEY = os.getenv("ANTILOPAY_PRIVATE_KEY", "")
ANTILOPAY_PROJECT_ID = os.getenv("ANTILOPAY_PROJECT_ID", "")
ANTILOPAY_WEBHOOK_URL = os.getenv("ANTILOPAY_WEBHOOK_URL", "https://thoughtfully-active-manakin.cloudpub.ru/bot_management/webhook/antilopay/")
ANTILOPAY_CALLBACK_PUBLIC_KEY = os.getenv("ANTILOPAY_CALLBACK_PUBLIC_KEY", "")

# Django API URL (настраиваемый)
DJANGO_API_URL = os.getenv("DJANGO_API_URL", "http://127.0.0.1:8123").rstrip('/')

# URL Next.js сайта (для верификации кода и подтверждения апгрейда)
SITE_URL = os.getenv("SITE_URL", "http://localhost:3000").rstrip('/')

# Remnawave API настройки для ОБХОДА (Night VPN)
REMNAWAVE_BYPASS_BASE_URL = os.getenv("REMNAWAVE_BYPASS_BASE_URL", "").strip() if os.getenv("REMNAWAVE_BYPASS_BASE_URL") else None
REMNAWAVE_BYPASS_API_KEY = os.getenv("REMNAWAVE_BYPASS_API_KEY", "").strip() if os.getenv("REMNAWAVE_BYPASS_API_KEY") else None
REMNAWAVE_BYPASS_DEFAULT_SQUAD_UUID = [u.strip() for u in os.getenv("REMNAWAVE_BYPASS_DEFAULT_SQUAD_UUID", "").split(",") if u.strip()] or None

# Remnawave Обычный VPN (использует тот же bypass API, но 1 squad)
REMNAWAVE_FAST_VPN_SQUAD_UUID = os.getenv("REMNAWAVE_FAST_VPN_SQUAD_UUID", "cf8da8a2-44cd-4a12-b568-7ccf6733eae2").strip() if os.getenv("REMNAWAVE_FAST_VPN_SQUAD_UUID", "cf8da8a2-44cd-4a12-b568-7ccf6733eae2") else None

# Remnawave сквад для пользователей с истекшей подпиской (режим ограниченного доступа)
REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID = os.getenv("REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID", "22a6415e-db7b-486c-8c8a-ccecf42d8459").strip() if os.getenv("REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID", "22a6415e-db7b-486c-8c8a-ccecf42d8459") else None

# FGN Connection API (устарело, не используется)
FGN_CONNECTION_BASE_URL = os.getenv("FGN_CONNECTION_BASE_URL", "https://aprs.fgnconnection.xyz").rstrip('/')
FGN_CONNECTION_TOKEN = os.getenv("FGN_CONNECTION_TOKEN", "")


# SOCKS5 прокси для Telegram API
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip() if os.getenv("TELEGRAM_PROXY") else None