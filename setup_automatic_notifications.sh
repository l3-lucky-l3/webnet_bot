#!/bin/bash

# Скрипт для настройки автоматических уведомлений на сервере
# Настраивает cron задачи для автоматических рассылок

echo "🚀 Настройка автоматических уведомлений..."

# Настройки путей
PROJECT_DIR=$(pwd)
CRON_WRAPPER="$PROJECT_DIR/cron_wrapper.sh"

echo "📁 Путь к проекту: $PROJECT_DIR"
echo "🔧 Путь к обертке: $CRON_WRAPPER"

# Проверяем наличие обертки
if [ ! -f "$CRON_WRAPPER" ]; then
    echo "❌ ОШИБКА: Обертка $CRON_WRAPPER не найдена!"
    exit 1
fi

if [ ! -x "$CRON_WRAPPER" ]; then
    echo "❌ ОШИБКА: Обертка $CRON_WRAPPER не исполняемая!"
    exit 1
fi

echo "✅ Обертка найдена и исполняемая"

# Создаем cron задачи для автоматических уведомлений

# 1. Напоминания о незавершенных платежах каждые 30 минут (только один раз на платеж)
CRON_JOB_1="*/30 * * * * $CRON_WRAPPER send_payment_reminders"

# 2. Напоминания о заканчивающихся подписках каждый день в 10:00 (только один раз на подписку)
CRON_JOB_2="0 10 * * * $CRON_WRAPPER send_subscription_reminders"

# 3. Проверка запасов ключей каждый час (повторяется при низком запасе)
CRON_JOB_3="0 * * * * $CRON_WRAPPER check_keys_availability"

# Добавляем задачи в crontab
echo "📝 Добавляем cron задачи:"

# Создаем временный файл с новыми задачами
TEMP_CRON=$(mktemp)
cat > "$TEMP_CRON" << EOF
$CRON_JOB_1
$CRON_JOB_2
$CRON_JOB_3
EOF

# Обновляем crontab
crontab "$TEMP_CRON"
rm "$TEMP_CRON"

# Создаем лог файлы
echo "📄 Создаем лог файлы..."
sudo touch /var/log/django_payment_reminders.log
sudo touch /var/log/django_subscription_reminders.log
sudo touch /var/log/django_keys_check.log
sudo touch /var/log/cron_wrapper.log

sudo chmod 666 /var/log/django_payment_reminders.log
sudo chmod 666 /var/log/django_subscription_reminders.log
sudo chmod 666 /var/log/django_keys_check.log
sudo chmod 666 /var/log/cron_wrapper.log

# Показываем настроенные задачи
echo ""
echo "✅ Настроенные автоматические задачи:"
echo ""
echo "🕐 Каждые 30 минут (*/30 * * * *):"
echo "   Напоминания о незавершенных платежах старше 30 минут"
echo ""
echo "🕐 Каждый день в 10:00 (0 10 * * *):"
echo "   Напоминания о подписках, заканчивающихся через 2 дня"
echo "   Уведомления о просроченных подписках"
echo ""
echo "🕐 Каждый час (0 * * * *):"
echo "   Проверка запасов VPN ключей (< 2 ключей)"
echo ""

# Показываем текущие cron задачи
echo "📋 Текущие cron задачи:"
crontab -l
echo ""

echo "📊 Лог файлы:"
echo "   /var/log/django_payment_reminders.log"
echo "   /var/log/django_subscription_reminders.log"
echo "   /var/log/django_keys_check.log"
echo "   /var/log/cron_wrapper.log"
echo ""

echo "🧪 Тестирование команд:"
echo "   python3 manage.py send_payment_reminders --dry-run"
echo "   python3 manage.py send_subscription_reminders --dry-run"
echo "   python3 manage.py check_keys_availability --dry-run"
echo ""
echo "🔍 Проверка работы cron:"
echo "   sudo tail -f /var/log/cron_wrapper.log"
echo "   sudo tail -f /var/log/django_payment_reminders.log"
echo ""
echo "📝 Особенности:"
echo "   • Напоминания о платежах: только один раз на платеж"
echo "   • Напоминания о подписках: только один раз на подписку"
echo "   • Уведомления о ключах: каждый час при низком запасе"
echo "   • Используется обертка cron_wrapper.sh для правильной работы"
echo ""

echo "✅ Настройка автоматических уведомлений завершена!"
echo ""
echo "🔍 Для проверки работы:"
echo "   sudo tail -f /var/log/django_payment_reminders.log"
echo "   sudo tail -f /var/log/django_subscription_reminders.log"
echo "   sudo tail -f /var/log/django_keys_check.log"


