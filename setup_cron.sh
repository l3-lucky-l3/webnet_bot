#!/bin/bash

# Скрипт для настройки автоматической очистки на сервере

echo "Настройка автоматической очистки системы..."

# Получаем путь к проекту
PROJECT_DIR=$(pwd)
PYTHON_PATH=$(which python3)

echo "Путь к проекту: $PROJECT_DIR"
echo "Путь к Python: $PYTHON_PATH"

# Создаем cron задачу для ежедневной очистки в 2:00 ночи
CRON_JOB="0 2 * * * cd $PROJECT_DIR && $PYTHON_PATH manage.py daily_cleanup >> /var/log/django_cleanup.log 2>&1"

# Добавляем задачу в crontab
(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

echo "Cron задача добавлена:"
echo "$CRON_JOB"

# Создаем лог файл
sudo touch /var/log/django_cleanup.log
sudo chmod 666 /var/log/django_cleanup.log

echo "Лог файл создан: /var/log/django_cleanup.log"

# Показываем текущие cron задачи
echo "Текущие cron задачи:"
crontab -l

echo "Настройка завершена!"
echo "Для проверки логов используйте: tail -f /var/log/django_cleanup.log"
