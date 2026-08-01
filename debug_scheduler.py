#!/usr/bin/env python3
"""
Отладочный скрипт для проверки работы планировщика на сервере
"""

import os
import sys
import json

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import django
django.setup()

def debug_scheduler():
    print("🐛 ОТЛАДКА ПЛАНИРОВЩИКА УВЕДОМЛЕНИЙ")
    print("=" * 50)

    try:
        from notification_scheduler import SCHEDULER_STATUS_FILE, NotificationScheduler, get_scheduler_status

        print(f"📁 SCHEDULER_STATUS_FILE: {SCHEDULER_STATUS_FILE}")
        print(f"📂 Текущая директория: {os.getcwd()}")
        print(f"📂 Директория скрипта: {os.path.dirname(__file__)}")
        print()

        # Проверяем файл
        if os.path.exists(SCHEDULER_STATUS_FILE):
            print(f"✅ Файл существует: {SCHEDULER_STATUS_FILE}")
            with open(SCHEDULER_STATUS_FILE, 'r') as f:
                data = json.load(f)
            print(f"📊 Содержимое файла: {json.dumps(data, indent=2, ensure_ascii=False)}")
        else:
            print(f"❌ Файл не найден: {SCHEDULER_STATUS_FILE}")
            # Ищем файл в других местах
            possible_paths = [
                os.path.join(os.getcwd(), 'scheduler_status.json'),
                '/root/scheduler_status.json',
                '/root/123/vpn night bot/vpn night bot1/scheduler_status.json',
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    print(f"✅ Найден в: {path}")
                    with open(path, 'r') as f:
                        data = json.load(f)
                    print(f"📊 Содержимое: {json.dumps(data, indent=2, ensure_ascii=False)}")
                    break
            else:
                print("❌ Файл не найден ни в одном месте")
                return

        print()

        # Проверяем функции
        print("🔧 Проверяем функции:")
        status = get_scheduler_status()
        print(f"get_scheduler_status(): {status}")

        file_status = NotificationScheduler.load_status_from_file()
        print(f"load_status_from_file(): {file_status}")

    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_scheduler()
