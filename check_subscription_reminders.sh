#!/bin/bash

# Скрипт для проверки уведомлений о подписках
# Использование: ./check_subscription_reminders.sh

echo "📅 ПРОВЕРКА УВЕДОМЛЕНИЙ О ПОДПИСКАХ"
echo "==================================="

# Активируем виртуальное окружение
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || echo "Виртуальное окружение не найдено"

# Запускаем Django команду
python3 manage.py send_subscription_reminders --dry-run

echo ""
echo "==================================="
echo "✅ ПРОВЕРКА ЗАВЕРШЕНА"

# Также можно запустить реальную отправку (ОСТОРОЖНО!)
echo ""
echo "⚠️  Для реальной отправки уведомлений используйте:"
echo "   python3 manage.py send_subscription_reminders"
echo ""
echo "🔄 Для сброса флагов уведомлений (тестирование):"
echo "   python3 manage.py send_subscription_reminders --reset-flags --dry-run"
echo ""
echo "⚙️  Для кастомных настроек:"
echo "   python3 manage.py send_subscription_reminders --days-before-expiry 1 --expired-days 2 --just-expired-hours 0 --dry-run"
