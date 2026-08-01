#!/bin/bash

# Скрипт для расчета времени до следующего запуска cron задач

echo "⏰ ВРЕМЯ ДО СЛЕДУЮЩЕГО ЗАПУСКА CRON ЗАДАЧ"
echo "=========================================="

CURRENT_TIME=$(date +%s)
CURRENT_MIN=$(date +%M)
CURRENT_HOUR=$(date +%H)

echo "📅 Текущее время: $(date '+%H:%M:%S')"
echo ""

# Расчет для send_payment_reminders (каждые 30 минут)
if [ $CURRENT_MIN -lt 30 ]; then
    PAYMENT_MINS=$((30 - CURRENT_MIN))
else
    PAYMENT_MINS=$((60 - CURRENT_MIN))
fi

PAYMENT_NEXT=$(date -d "+$PAYMENT_MINS minutes" "+%H:%M")
echo "🔄 Напоминания о платежах (каждые 30 мин):"
echo "   Через: $PAYMENT_MINS мин"
echo "   Следующий запуск: $PAYMENT_NEXT"
echo ""

# Расчет для check_keys_availability (каждый час)
KEYS_MINS=$((60 - CURRENT_MIN))
if [ $KEYS_MINS -eq 60 ]; then
    KEYS_MINS=0
fi

KEYS_NEXT=$(date -d "+$KEYS_MINS minutes" "+%H:%M")
echo "🔑 Проверка ключей (каждый час):"
echo "   Через: $KEYS_MINS мин"
echo "   Следующий запуск: $KEYS_NEXT"
echo ""

# Расчет для send_subscription_reminders (10:00 каждый день)
if [ $CURRENT_HOUR -lt 10 ]; then
    SUB_HOURS=$((10 - CURRENT_HOUR))
    SUB_MINS=$((SUB_HOURS * 60 - CURRENT_MIN))
else
    SUB_HOURS=$((24 - CURRENT_HOUR + 10))
    SUB_MINS=$((SUB_HOURS * 60 - CURRENT_MIN))
fi

SUB_HOURS_DISPLAY=$((SUB_MINS / 60))
SUB_MINS_DISPLAY=$((SUB_MINS % 60))

echo "📅 Напоминания о подписках (10:00 каждый день):"
if [ $SUB_HOURS_DISPLAY -gt 0 ]; then
    echo "   Через: $SUB_HOURS_DISPLAY ч $SUB_MINS_DISPLAY мин"
else
    echo "   Через: $SUB_MINS_DISPLAY мин"
fi
echo "   Следующий запуск: 10:00"
echo ""

echo "📋 Для просмотра логов:"
echo "   tail -f logs/cron_wrapper.log"
echo "   ./check_notifications.sh"
echo "   python3 test_scheduler.py"
echo ""
echo "💡 Планировщик работает внутри бота и не требует cron!"
