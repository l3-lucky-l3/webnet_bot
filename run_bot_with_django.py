#!/usr/bin/env python
"""
Скрипт для запуска бота с интеграцией Django
"""
import asyncio
import os
import sys
import subprocess
import time
import platform
from dotenv import load_dotenv
import django

# Загружаем .env перед инициализацией Django
load_dotenv()

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
django.setup()

def run_django_server():
    """Запуск Django сервера в отдельном процессе"""
    try:
        print("🌐 Запуск Django сервера...")
        
        # Определяем команду для запуска в зависимости от ОС
        if platform.system() == "Windows":
            # На Windows используем start для запуска в новом окне
            subprocess.Popen(['start', 'cmd', '/c', 'python manage.py runserver 8123'], 
                           shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # На Unix-системах используем nohup
            subprocess.Popen(['nohup', sys.executable, 'manage.py', 'runserver', '8123'], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return True
    except Exception as e:
        print(f"❌ Ошибка запуска Django: {e}")
        return False

def run_bot():
    """Запуск бота"""
    try:
        print("🤖 Запуск Telegram бота...")
        from bot_with_django import main
        asyncio.run(main())
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")

if __name__ == "__main__":
    print("🚀 Запуск системы: Django + Telegram Bot")
    print("=" * 50)
    
    # Запускаем Django в отдельном процессе
    if run_django_server():
        # Ждем немного, чтобы Django успел запуститься
        print("⏳ Ожидание запуска Django сервера...")
        time.sleep(8)
        
        print("✅ Django сервер должен быть запущен на http://127.0.0.1:8123/")
        print("✅ Telegram бот запущен")
        print("=" * 50)
        print("Для остановки нажмите Ctrl+C")
        print("Веб-админка: http://127.0.0.1:8123/")
        print("Логин: admin / Пароль: admin123")
        print("=" * 50)
        print("💡 Если сайт не открывается, запустите Django отдельно:")
        print("   python manage.py runserver 8123")
        print("=" * 50)
        
        try:
            # Запускаем бота в основном потоке
            run_bot()
        except KeyboardInterrupt:
            print("\n🛑 Остановка бота...")
            print("💡 Для остановки Django закройте его окно или нажмите Ctrl+C в том окне")
            sys.exit(0)
    else:
        print("❌ Не удалось запустить Django сервер")
        print("💡 Попробуйте запустить Django вручную: python manage.py runserver 8123")
        sys.exit(1)
