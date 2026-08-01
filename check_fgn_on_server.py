#!/usr/bin/env python3
"""
Тест FGN Connection API на сервере.
Запуск: python3 check_fgn_on_server.py
"""

import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')

import django
django.setup()

from config import FGN_CONNECTION_TOKEN, FGN_CONNECTION_BASE_URL

print("=" * 60)
print("FGN Connection API - проверка на сервере")
print("=" * 60)

print(f"\nBase URL: {FGN_CONNECTION_BASE_URL}")
print(f"Token: {repr(FGN_CONNECTION_TOKEN)[:20]}... ({len(FGN_CONNECTION_TOKEN)} chars)")
print(f"Token length: {len(FGN_CONNECTION_TOKEN) if FGN_CONNECTION_TOKEN else 0}")

if not FGN_CONNECTION_TOKEN:
    print("\n❌ ТОКЕН НЕ УСТАНОВЛЕН!")
    print("Проверь .env файл: grep FGN_CONNECTION_TOKEN /root/zerkalo/.env")
    sys.exit(1)

from bot_management.fgn_connection_api import get_api, FgnConnectionAPIError

api = get_api()
test_tg_id = 6484952272

def extract_key(response):
    """Простая функция извлечения ключа"""
    # Сначала ищем прямые поля
    if 'key' in response and response['key']:
        key = response['key']
    elif 'subscription_url' in response:
        key = response['subscription_url']
    elif 'url' in response:
        key = response['url']
    else:
        # Строим из id если есть CREATED/TRIAL
        info = response.get('info', '')
        sub_id = response.get('id')
        if sub_id and ('CREATED' in info or 'TRIAL' in info):
            key = f"https://webnetvpn.great-connection.net/{sub_id}"
        else:
            key = None
    
    if key and 'fgn.great-connection.net' in key:
        key = key.replace('fgn.great-connection.net', 'webnetvpn.great-connection.net')
    return key

print(f"\n{'=' * 60}")
print(f"Тест 1: /anti/trial (TG ID: {test_tg_id})")
print(f"{'=' * 60}")

try:
    resp = api.create_trial(tg_id=test_tg_id)
    print(f"Raw response: {resp}")
    
    key = extract_key(resp)
    print(f"Extracted key: {key}")
    
    if key:
        print("✅ TRIAL СОЗДАН УСПЕШНО!")
    else:
        print("❌ Не удалось извлечь ключ из ответа")
except FgnConnectionAPIError as e:
    print(f"❌ FGN API Error: {e.message}")
    print(f"   Response data: {e.response_data}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'=' * 60}")
print(f"Тест 2: /anti/create (TG ID: 77777777, 1 month)")
print(f"{'=' * 60}")

try:
    resp2 = api.create_key(tg_id=77777777, months=1)
    print(f"Raw response: {resp2}")
    
    key2 = extract_key(resp2)
    print(f"Extracted key: {key2}")
    
    if key2:
        print("✅ KEY СОЗДАН УСПЕШНО!")
    else:
        print("❌ Не удалось извлечь ключ из ответа")
except FgnConnectionAPIError as e:
    print(f"❌ FGN API Error: {e.message}")
    print(f"   Response data: {e.response_data}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'=' * 60}")
print("Проверка завершена")
print(f"{'=' * 60}")
