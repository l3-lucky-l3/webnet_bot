@echo off
echo 🚀 Запуск полной системы: Django + Telegram Bot
echo ================================================

echo 🌐 Запуск Django сервера в фоне...
start "Django Server" cmd /c "python manage.py runserver 8123"

echo ⏳ Ожидание запуска Django...
timeout /t 5 /nobreak >nul

echo 🤖 Запуск Telegram бота...
python bot_with_django.py

echo 🛑 Остановка системы...
taskkill /F /IM python.exe >nul 2>&1
pause
