#!/bin/bash

# Обертка для cron задач Django
# Этот скрипт должен запускаться из cron вместо прямых команд Django

# Настройки
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_PATH="/usr/bin/python3"

# Функция логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_DIR/cron_wrapper.log"
}

log "=== Начало выполнения cron задачи ==="
log "PROJECT_DIR: $PROJECT_DIR"
log "USER: $(whoami)"
log "PWD: $(pwd)"

# Переходим в директорию проекта
cd "$PROJECT_DIR" || {
    log "ОШИБКА: Не удалось перейти в директорию $PROJECT_DIR"
    exit 1
}

log "Успешно перешли в директорию проекта"

# Проверяем наличие manage.py
if [ ! -f "manage.py" ]; then
    log "ОШИБКА: manage.py не найден в $(pwd)"
    exit 1
fi

# Загружаем переменные окружения из .env файла (если существует)
if [ -f ".env" ] && [ -r ".env" ]; then
    log "Загружаем переменные из .env файла"
    set -a
    # Используем более безопасный способ загрузки .env
    while IFS='=' read -r key value; do
        # Пропускаем комментарии и пустые строки
        [[ $key =~ ^[[:space:]]*# ]] && continue
        [[ -z $key ]] && continue
        # Убираем пробелы вокруг
        key=$(echo "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        value=$(echo "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        # Экспортируем переменную
        export "$key=$value"
    done < .env
    set +a
    log "Переменные окружения загружены"
else
    log "ПРЕДУПРЕЖДЕНИЕ: .env файл не найден или не читаем"
fi

# Устанавливаем необходимые переменные окружения для Django
# Делаем это ПОСЛЕ загрузки .env чтобы переопределить любые настройки оттуда
# Принудительно устанавливаем SQLite настройки
export DJANGO_SETTINGS_MODULE="tg_bot_admin.settings"
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

# Убеждаемся что нет переопределения из .env или других источников
unset DJANGO_SETTINGS_MODULE 2>/dev/null || true
export DJANGO_SETTINGS_MODULE="tg_bot_admin.settings"

log "DJANGO_SETTINGS_MODULE: $DJANGO_SETTINGS_MODULE"
log "PYTHONPATH: $PYTHONPATH"

# Логируем все переменные окружения содержащие DJANGO
env | grep -i django | while read line; do log "ENV: $line"; done

# Проверяем аргументы командной строки
COMMAND="$1"
shift

case "$COMMAND" in
    "send_payment_reminders")
        log "Запуск send_payment_reminders с аргументами: $*"
        $PYTHON_PATH manage.py send_payment_reminders "$@" >> "$LOG_DIR/django_payment_reminders.log" 2>&1
        EXIT_CODE=$?
        ;;

    "send_subscription_reminders")
        log "Запуск send_subscription_reminders с аргументами: $*"
        $PYTHON_PATH manage.py send_subscription_reminders "$@" >> "$LOG_DIR/django_subscription_reminders.log" 2>&1
        EXIT_CODE=$?
        ;;

    "check_keys_availability")
        log "Запуск check_keys_availability с аргументами: $*"
        $PYTHON_PATH manage.py check_keys_availability "$@" >> "$LOG_DIR/django_keys_check.log" 2>&1
        EXIT_CODE=$?
        ;;

    *)
        log "ОШИБКА: Неизвестная команда: $COMMAND"
        echo "Использование: $0 {send_payment_reminders|send_subscription_reminders|check_keys_availability} [аргументы]"
        exit 1
        ;;
esac

if [ $EXIT_CODE -eq 0 ]; then
    log "Команда $COMMAND выполнена успешно"
else
    log "ОШИБКА: Команда $COMMAND завершилась с кодом $EXIT_CODE"
fi

log "=== Завершение выполнения cron задачи ==="
exit $EXIT_CODE
