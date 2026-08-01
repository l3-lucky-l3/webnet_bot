"""
WSGI конфигурация для VPN бота.
"""
import os
from django.core.wsgi import get_wsgi_application

# Устанавливаем настройки для продакшена
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings_production')

application = get_wsgi_application()

