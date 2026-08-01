#!/bin/bash

# Скрипт для проверки статуса уведомлений
# Использование: ./check_notifications.sh

echo "🔍 ПРОВЕРКА СТАТУСА УВЕДОМЛЕНИЙ"
echo "=================================="

# Проверяем логи cron
echo ""
echo "📋 Последние записи в cron_wrapper.log:"
if [ -f "logs/cron_wrapper.log" ]; then
    tail -5 logs/cron_wrapper.log
else
    echo "❌ Файл лога не найден"
fi

# Проверяем логи ключей
echo ""
echo "🔑 Последние записи в django_keys_check.log:"
if [ -f "logs/django_keys_check.log" ]; then
    tail -5 logs/django_keys_check.log | grep -v "client_session\|Unclosed\|connections\|connector"
else
    echo "❌ Файл лога не найден"
fi

# Проверяем логи платежей
echo ""
echo "💳 Последние записи в django_payment_reminders.log:"
if [ -f "logs/django_payment_reminders.log" ]; then
    tail -3 logs/django_payment_reminders.log
else
    echo "❌ Файл лога не найден"
fi

# Проверяем логи подписок
echo ""
echo "📅 Последние записи в django_subscription_reminders.log:"
if [ -f "logs/django_subscription_reminders.log" ]; then
    tail -3 logs/django_subscription_reminders.log
else
    echo "❌ Файл лога не найден"
fi

# Текущий статус ключей
echo ""
echo "📊 ТЕКУЩИЙ СТАТУС КЛЮЧЕЙ:"
echo "-------------------------"
python3 manage.py check_keys_availability --dry-run 2>/dev/null | grep -E "(Тип|Будет отправлено|Предварительный просмотр)"

# Статус планировщика уведомлений
echo ""
echo "🔔 ПЛАНИРОВЩИК APSCHEDULER:"
echo "---------------------------"
echo "📊 Система уведомлений работает внутри бота (APScheduler)"
echo "🎯 Автоматические задачи:"
echo "   • 💳 Напоминания о платежах (каждые 30 мин)"
echo "   • 🔑 Проверка ключей (каждый час)"
echo "   • 📅 Напоминания о подписках (10:00 ежедневно)"
echo ""
echo "📈 Для просмотра детального статуса используйте админ-панель бота"

echo ""
echo "✅ Проверка завершена!"
