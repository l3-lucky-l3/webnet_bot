#!/bin/bash

# Скрипт деплоя VPN бота на сервер
# Версия: 2.0

set -e

echo "🚀 Начинаем деплой VPN бота..."

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Проверяем, что мы в правильной директории
if [ ! -f "manage.py" ]; then
    error "manage.py не найден. Запустите скрипт из корневой директории проекта."
    exit 1
fi

# Проверяем наличие .env файла
if [ ! -f ".env" ]; then
    warn "Файл .env не найден. Создаем из примера..."
    if [ -f "env.production.example" ]; then
        cp env.production.example .env
        warn "Файл .env создан из примера. ОБЯЗАТЕЛЬНО отредактируйте его перед запуском!"
    else
        error "Файл env.production.example не найден. Создайте файл .env вручную."
        exit 1
    fi
fi

# Проверяем права на файлы
log "Проверяем права на файлы..."
chmod +x deploy.sh
chmod 644 .env

# Создаем необходимые директории
log "Создаем необходимые директории..."
mkdir -p logs
mkdir -p staticfiles
mkdir -p media
mkdir -p videos
mkdir -p images
chmod 755 logs staticfiles media videos images

# Активируем виртуальное окружение
log "Активируем виртуальное окружение..."
if [ ! -d "venv" ]; then
    warn "Виртуальное окружение не найдено. Создаем..."
    python3 -m venv venv
fi
source venv/bin/activate

# Обновляем pip
log "Обновляем pip..."
pip install --upgrade pip

# Устанавливаем/обновляем зависимости
log "Устанавливаем зависимости..."
pip install -r requirements.txt

# Проверяем подключение к базе данных
log "Проверяем подключение к базе данных..."
python manage.py check --settings=tg_bot_admin.settings_production || warn "Проблемы с настройками Django"

# Собираем статические файлы
log "Собираем статические файлы..."
python manage.py collectstatic --noinput --settings=tg_bot_admin.settings_production

# Применяем миграции
log "Применяем миграции..."
python manage.py migrate --settings=tg_bot_admin.settings_production

# Создаем суперпользователя (если нужно)
info "Создание суперпользователя (нажмите Ctrl+C чтобы пропустить)..."
python manage.py createsuperuser --settings=tg_bot_admin.settings_production || warn "Создание суперпользователя пропущено"

# Проверяем конфигурацию Nginx
log "Проверяем конфигурацию Nginx..."
if sudo nginx -t 2>/dev/null; then
    log "Конфигурация Nginx корректна"
else
    warn "Проблемы с конфигурацией Nginx"
fi

# Перезапускаем сервисы
log "Перезапускаем сервисы..."
sudo systemctl restart vpn-bot || warn "Не удалось перезапустить vpn-bot сервис"
sudo systemctl restart nginx || warn "Не удалось перезапустить nginx"

# Проверяем статус сервисов
log "Проверяем статус сервисов..."
if sudo systemctl is-active --quiet vpn-bot; then
    log "✅ Сервис vpn-bot запущен"
else
    error "❌ Сервис vpn-bot не запущен"
fi

if sudo systemctl is-active --quiet nginx; then
    log "✅ Сервис nginx запущен"
else
    error "❌ Сервис nginx не запущен"
fi

# Проверяем порты
log "Проверяем порты..."
if netstat -tlnp | grep -q ":8123 "; then
    log "✅ Порт 8123 открыт"
else
    warn "⚠️ Порт 8123 не открыт"
fi

if netstat -tlnp | grep -q ":80 "; then
    log "✅ Порт 80 открыт"
else
    warn "⚠️ Порт 80 не открыт"
fi

log "✅ Деплой завершен успешно!"
log ""
log "📋 Следующие шаги:"
log "1. Проверьте логи: sudo journalctl -u vpn-bot -f"
log "2. Проверьте веб-интерфейс: http://your-domain.com/admin/"
log "3. Проверьте работу бота: отправьте /start боту"
log "4. Настройте SSL сертификат если нужно"
log ""
log "🔧 Полезные команды:"
log "- Перезапуск бота: sudo systemctl restart vpn-bot"
log "- Логи бота: sudo journalctl -u vpn-bot -f"
log "- Логи Nginx: sudo tail -f /var/log/nginx/vpn-bot_error.log"
log "- Статус сервисов: sudo systemctl status vpn-bot nginx"

