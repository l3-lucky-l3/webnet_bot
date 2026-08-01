#!/bin/bash

# Скрипт для проверки уведомлений о закончившихся trial ключах
# Использование: ./check_trial_keys_expired.sh

echo "🎁 ПРОВЕРКА УВЕДОМЛЕНИЙ О ЗАКОНЧИВШИХСЯ TRIAL КЛЮЧАХ"
echo "===================================================="

# Активируем виртуальное окружение
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || echo "Виртуальное окружение не найдено"

# Запускаем Django команду
python3 manage.py notify_trial_keys_expired --dry-run

echo ""
echo "===================================================="
echo "✅ ПРОВЕРКА ЗАВЕРШЕНА"

# Также можно запустить реальную отправку (ОСТОРОЖНО!)
echo ""
echo "⚠️  Для реальной отправки уведомлений используйте:"
echo "   python3 manage.py notify_trial_keys_expired"
echo ""
echo "🔄 Для сброса флагов уведомлений (тестирование):"
echo "   python3 manage.py notify_trial_keys_expired --reset-flags --dry-run"
echo ""
echo "⚙️  Для кастомных настроек (пример - через 48 часов):"
echo "   python3 manage.py notify_trial_keys_expired --hours-after-expiry 48 --tolerance-hours 2 --dry-run"
