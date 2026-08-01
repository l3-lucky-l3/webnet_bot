#!/bin/bash

# Скрипт для проверки статистики платежей за сегодня
# Использование: ./check_payment_stats_today.sh

echo "📊 ПРОВЕРКА СТАТИСТИКИ ПЛАТЕЖЕЙ ЗА СЕГОДНЯ"
echo "=========================================="

# Активируем виртуальное окружение
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || echo "Виртуальное окружение не найдено"

# Запускаем Django команду
python3 manage.py payment_stats_today

echo ""
echo "=========================================="
echo "✅ ПРОВЕРКА ЗАВЕРШЕНА"

# Также можно получить статистику через API (если сервер запущен)
echo ""
echo "💡 API endpoint: /bot/api/payments/stats/today/"
echo "   Для получения JSON данных используйте:"
echo "   curl http://127.0.0.1:8123/bot/api/payments/stats/today/"
