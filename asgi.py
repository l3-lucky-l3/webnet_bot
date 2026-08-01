"""
ASGI конфигурация для VPN бота.
"""
import os
from django.core.asgi import get_asgi_application

# Устанавливаем настройки для продакшена
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings_production')

application = get_asgi_application()

