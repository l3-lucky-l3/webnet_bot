#!/usr/bin/env python3
"""
Скрипт для проверки статуса планировщика уведомлений в реальном времени
Использование: python3 check_scheduler_status.py
"""

import asyncio
import sys
import os
import requests
import json
from datetime import datetime

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
import django
django.setup()

def check_scheduler_via_api():
    """
    Проверяет статус планировщика через Django API
    """
    try:
        # Пытаемся получить статус через API
        response = requests.get('http://127.0.0.1:8123/bot_management/api/scheduler_status/', timeout=5)

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            return {"error": f"HTTP {response.status_code}"}

    except requests.exceptions.RequestException as e:
        return {"error": f"Connection failed: {e}"}

def check_scheduler_via_module():
    """
    Проверяет статус планировщика напрямую через модуль
    """
    try:
        from notification_scheduler import get_scheduler_status, SCHEDULER_STATUS_FILE, NotificationScheduler

        # Сначала пытаемся получить статус от активного планировщика
        status = get_scheduler_status()

        # Если планировщик не активен (UNKNOWN), пробуем загрузить из файла напрямую
        if status.get('status') == 'unknown' or status.get('jobs_count', 0) == 0:
            file_status = NotificationScheduler.load_status_from_file()
            if file_status.get('status') != 'unknown':
                status = file_status

        # Добавляем информацию о пути к файлу
        status['_status_file_path'] = SCHEDULER_STATUS_FILE
        status['_current_dir'] = os.getcwd()

        return status
    except Exception as e:
        return {"error": f"Module error: {e}"}

def format_scheduler_status(status_data):
    """
    Форматирует данные статуса планировщика для вывода
    """
    print("🔔 СТАТУС ПЛАНИРОВЩИКА УВЕДОМЛЕНИЙ")
    print("=" * 50)

    if "error" in status_data:
        print(f"❌ ОШИБКА: {status_data['error']}")
        print()
        print("💡 ВОЗМОЖНЫЕ ПРИЧИНЫ:")
        print("• Бот не запущен")
        print("• Django сервер не работает")
        print("• Планировщик не инициализирован")
        return

    # Основной статус
    status = status_data.get('status', 'unknown')
    status_emoji = "🟢" if status == "running" else "🔴"

    print(f"📊 Общий статус: {status_emoji} {status.upper()}")
    print(f"🎯 Количество задач: {status_data.get('jobs_count', 0)}")

    # Диагностическая информация
    if '_status_file_path' in status_data:
        print(f"🔍 Диагностика файла статуса:")
        print(f"📁 Путь к файлу: {status_data.get('_status_file_path', 'N/A')}")
        print(f"📂 Текущая директория: {status_data.get('_current_dir', 'N/A')}")
        print()

    print()

    # Задачи
    jobs = status_data.get('jobs', [])
    if jobs:
        print("📋 АКТИВНЫЕ ЗАДАЧИ:")
        print("-" * 30)

        for job in jobs:
            print(f"🔸 {job['name']}")
            print(f"   🆔 ID: {job['id']}")

            # Форматируем время следующего запуска
            next_run = job.get('next_run')
            if next_run:
                try:
                    dt = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
                    next_run_formatted = dt.strftime('%d.%m.%Y %H:%M:%S')
                    print(f"   🕐 Следующий запуск: {next_run_formatted}")
                except:
                    print(f"   🕐 Следующий запуск: {next_run}")
            else:
                print("   🕐 Следующий запуск: Не запланировано")

            # Преобразуем trigger в читаемый вид
            trigger = job['trigger']
            if 'cron' in trigger:
                if 'minute=' in trigger and 'hour=' in trigger:
                    print("   ⏰ Расписание: Ежедневно в 10:00")
                elif 'minute=' in trigger and '*/30' in trigger:
                    print("   ⏰ Расписание: Каждые 30 минут")
                elif 'minute=' in trigger and '0' in trigger and 'hour=' not in trigger:
                    print("   ⏰ Расписание: Каждый час")
                else:
                    print(f"   ⏰ Расписание: {trigger}")
            else:
                print(f"   ⏰ Расписание: {trigger}")
            print()
    else:
        print("❌ Активных задач не найдено")

def check_recent_logs():
    """
    Проверяет недавние логи планировщика
    """
    print("\n📄 ПОСЛЕДНИЕ ЛОГИ:")
    print("-" * 20)

    log_files = [
        "logs/cron_wrapper.log",
        "logs/django_payment_reminders.log",
        "logs/django_keys_check.log",
        "logs/django_subscription_reminders.log"
    ]

    for log_file in log_files:
        if os.path.exists(log_file):
            print(f"\n🔍 {log_file}:")
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()[-3:]  # Последние 3 строки
                    for line in lines:
                        print(f"   {line.strip()}")
            except Exception as e:
                print(f"   ❌ Ошибка чтения: {e}")
        else:
            print(f"🔍 {log_file}: Файл не найден")

def main():
    """
    Основная функция проверки
    """
    print(f"🔍 ПРОВЕРКА СТАТУСА ПЛАНИРОВЩИКА - {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print()

    # Проверяем через API
    print("🌐 ПРОВЕРКА ЧЕРЕЗ API:")
    api_status = check_scheduler_via_api()
    format_scheduler_status(api_status)

    # Если API не работает или возвращает пустой/неправильный результат, пробуем напрямую
    if not api_status or api_status.get('status') == 'unknown' or "error" in api_status:
        print("\n🔧 ПРОВЕРКА НАПРЯМУЮ:")
        direct_status = check_scheduler_via_module()
        format_scheduler_status(direct_status)
    else:
        print("✅ API работает, используем данные из API")

    # Проверяем логи
    check_recent_logs()

    print("\n" + "=" * 50)
    print("✅ ПРОВЕРКА ЗАВЕРШЕНА")

    # Рекомендации
    print("\n💡 РЕКОМЕНДАЦИИ:")
    if "error" in api_status:
        print("• Запустите бота: python bot_with_django.py")
        print("• Проверьте Django сервер: python manage.py runserver")
    else:
        status = api_status.get('status')
        if status == "running":
            print("• Планировщик работает нормально ✅")
        else:
            print("• Планировщик остановлен, проверьте логи бота")

if __name__ == "__main__":
    main()
