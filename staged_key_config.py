import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

# Remnawave Regular VPN
REMNAWAVE_BASE_URL = (os.getenv("REMNAWAVE_BASE_URL") or "").strip().rstrip('/') or None
REMNAWAVE_API_KEY = (os.getenv("REMNAWAVE_API_KEY") or "").strip() or None
REMNAWAVE_DEFAULT_SQUAD_UUID = (os.getenv("REMNAWAVE_DEFAULT_SQUAD_UUID") or "").strip() or None

# Remnawave Bypass (Night VPN)
REMNAWAVE_BYPASS_BASE_URL = (os.getenv("REMNAWAVE_BYPASS_BASE_URL") or "").strip().rstrip('/') or None
REMNAWAVE_BYPASS_API_KEY = (os.getenv("REMNAWAVE_BYPASS_API_KEY") or "").strip() or None
REMNAWAVE_BYPASS_SQUAD_UUIDS = [u.strip() for u in os.getenv("REMNAWAVE_BYPASS_DEFAULT_SQUAD_UUID", "").split(",") if u.strip()] or None

# Remnawave Обычный VPN
REMNAWAVE_FAST_SQUAD_UUID = (os.getenv("REMNAWAVE_FAST_VPN_SQUAD_UUID") or "").strip() or None

# Telegram for subscription check (Новый бот)
VERIFY_BOT_TOKEN = os.getenv("VERIFY_BOT_TOKEN") or ""

# Telegram channel
REQUIRED_CHANNEL = "@webnetru"
REQUIRED_CHANNEL_ID = -1003474889644

# Flask server
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5050

# Duration
TEMP_KEY_DAYS = 1
FULL_KEY_DAYS = 30

# Secret code length
CODE_LENGTH = 8
