#!/bin/bash

# Скрипт для быстрого развертывания защиты от DDoS на сервере

echo "🛡️ РАЗВЕРТЫВАНИЕ ЗАЩИТЫ ОТ DDoS"
echo "================================"

SERVER="wheat-hiddenite"  # Замените на ваш сервер
REMOTE_PATH="/root/123/vpn night bot/vpn night bot1"

echo "📤 Загружаем обновленные файлы..."

# Загружаем обновленные файлы
scp bot_management/bot_security.py root@$SERVER:"$REMOTE_PATH/bot_management/"
scp bot_management/bot_middlewares.py root@$SERVER:"$REMOTE_PATH/bot_management/"
scp bot_with_django.py root@$SERVER:"$REMOTE_PATH/"
scp check_ddos_protection.py root@$SERVER:"$REMOTE_PATH/"
scp DDOS_PROTECTION_README.md root@$SERVER:"$REMOTE_PATH/"

echo ""
echo "🔄 Перезапускаем бота..."

# Подключаемся к серверу и перезапускаем
ssh root@$SERVER << 'EOF'
    cd "/root/123/vpn night bot/vpn night bot1"

    # Останавливаем текущий бот (если запущен)
    pkill -f "python3.*bot_with_django.py" || true

    # Активируем виртуальное окружение
    source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || echo "Виртуальное окружение не найдено"

    # Проверяем защиту
    echo "Проверяем защиту..."
    python3 check_ddos_protection.py

    # Запускаем бота в фоне
    echo "Запускаем бота..."
    nohup python3 bot_with_django.py > bot.log 2>&1 &

    echo "✅ Защита от DDoS развернута!"
EOF

echo ""
echo "✅ Развертывание завершено!"
echo ""
echo "🔍 Для проверки состояния выполните на сервере:"
echo "cd '$REMOTE_PATH' && python3 check_ddos_protection.py"
echo ""
echo "📖 Документацию читайте в DDOS_PROTECTION_README.md"
