#!/bin/bash

# Скрипт для проверки статуса системы уведомлений

echo "🔍 ПРОВЕРКА СИСТЕМЫ АВТОМАТИЧЕСКИХ УВЕДОМЛЕНИЙ"
echo "=============================================="
echo ""

echo "1️⃣ Статус планировщика:"
python3 -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings_production')
import django
django.setup()
from notification_scheduler import get_scheduler_status
status = get_scheduler_status()
print(f'   📊 Статус: {status.get(\"status\", \"unknown\")}')
print(f'   🎯 Задач: {status.get(\"jobs_count\", 0)}')
for job in status.get('jobs', []):
    next_run = job.get('next_run', 'unknown')[:19] if job.get('next_run') else 'unknown'
    print(f'   • {job[\"name\"]} → {next_run}')
" 2>/dev/null

echo ""
echo "2️⃣ Подписок для уведомлений:"
python3 manage.py shell -c "
from bot_management.models import Payment
from django.utils import timezone
from datetime import timedelta

# Подписки, закончившиеся 46-50 часов назад
just_expired_min = timezone.now() - timedelta(hours=50)
just_expired_max = timezone.now() - timedelta(hours=46)

expired_count = Payment.objects.filter(
    subscription_expires_at__range=(just_expired_min, just_expired_max),
    status='succeeded',
    subscription_just_expired_notified=False
).count()

print(f'   📧 Пользователям: {expired_count}')

# Trial ключи для админов (старше 24 часов)
old_trial = Payment.objects.filter(
    subscription_expires_at__lte=timezone.now() - timedelta(hours=24),
    status='succeeded',
    subscription_type='trial',
    trial_key_expired_admin_notified=False
).count()

print(f'   👑 Админам о trial: {old_trial}')
" 2>/dev/null

echo ""
echo "3️⃣ Тестовый запуск (без отправки):"
python3 manage.py send_subscription_reminders --days-before-expiry=999 --expired-days=999 --just-expired-hours=48 --just-expired-tolerance-hours=2 --dry-run 2>/dev/null | grep -E "(Найдено|Будет отправлено)" | head -3

echo ""
echo "4️⃣ Логи планировщика:"
if [ -f "logs/django_subscription_reminders.log" ]; then
    echo "   📄 Последние записи:"
    tail -3 logs/django_subscription_reminders.log 2>/dev/null | sed 's/^/      /'
else
    echo "   📭 Лог еще не создан"
fi

echo ""
echo "✅ ПРОВЕРКА ЗАВЕРШЕНА"
echo ""
echo "💡 Система работает автоматически если:"
echo "   • Планировщик в статусе 'running'"
echo "   • Есть задачи в расписании"
echo "   • Есть подписки для уведомлений"
echo "   • Команды находят подписки при тесте"
