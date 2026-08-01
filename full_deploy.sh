#!/bin/bash

# Скрипт полного деплоя VPN бота на Linux сервер
# Версия: 1.0

set -e

echo "🚀 Полный деплой VPN бота на Linux сервер"
echo "=========================================="

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

# Проверяем, что мы запущены от root или с sudo
if [ "$EUID" -ne 0 ]; then
    error "Этот скрипт должен быть запущен с правами root или через sudo"
    exit 1
fi

# Параметры
DOMAIN=""
BOT_TOKEN=""
YOOKASSA_SHOP_ID=""
YOOKASSA_SECRET_KEY=""

# Запрашиваем параметры
if [ -z "$DOMAIN" ]; then
    read -p "Введите ваш домен (например: example.com): " DOMAIN
fi

if [ -z "$BOT_TOKEN" ]; then
    read -p "Введите токен Telegram бота: " BOT_TOKEN
fi

if [ -z "$YOOKASSA_SHOP_ID" ]; then
    read -p "Введите Shop ID ЮKassa: " YOOKASSA_SHOP_ID
fi

if [ -z "$YOOKASSA_SECRET_KEY" ]; then
    read -p "Введите Secret Key ЮKassa: " YOOKASSA_SECRET_KEY
fi

log "Начинаем деплой с параметрами:"
log "- Домен: $DOMAIN"
log "- Бот токен: ${BOT_TOKEN:0:10}..."
log "- Shop ID: $YOOKASSA_SHOP_ID"

# Шаг 1: Установка зависимостей
log "Шаг 1: Установка зависимостей..."
if [ -f "install_dependencies.sh" ]; then
    chmod +x install_dependencies.sh
    ./install_dependencies.sh
else
    error "Файл install_dependencies.sh не найден"
    exit 1
fi

# Шаг 2: Настройка базы данных
log "Шаг 2: Настройка базы данных..."
if [ -f "setup_database.sh" ]; then
    chmod +x setup_database.sh
    ./setup_database.sh
else
    error "Файл setup_database.sh не найден"
    exit 1
fi

# Шаг 3: Настройка проекта
log "Шаг 3: Настройка проекта..."

# Создаем директорию проекта
mkdir -p /var/www/vpn-bot
cd /var/www/vpn-bot

# Копируем файлы проекта (предполагаем, что скрипт запущен из директории проекта)
if [ -f "../manage.py" ]; then
    cp -r ../* /var/www/vpn-bot/
else
    warn "Файлы проекта не найдены. Убедитесь, что скрипт запущен из директории проекта"
fi

# Настраиваем права
chown -R www-data:www-data /var/www/vpn-bot
chmod -R 755 /var/www/vpn-bot

# Создаем .env файл
log "Создаем файл .env..."
if [ -f "env.production.example" ]; then
    cp env.production.example .env
    
    # Получаем данные базы данных
    DB_PASSWORD=$(grep "DB_PASSWORD=" database_config.txt 2>/dev/null | cut -d'=' -f2 || echo "your-database-password")
    
    # Генерируем SECRET_KEY
    SECRET_KEY=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-50)
    
    # Обновляем .env файл
    sed -i "s/your-very-secret-key-here-change-this/$SECRET_KEY/" .env
    sed -i "s/your-domain.com/$DOMAIN/g" .env
    sed -i "s/your-telegram-bot-token/$BOT_TOKEN/" .env
    sed -i "s/your-shop-id/$YOOKASSA_SHOP_ID/" .env
    sed -i "s/your-secret-key/$YOOKASSA_SECRET_KEY/" .env
    sed -i "s/your-database-password/$DB_PASSWORD/" .env
    
    log "✅ Файл .env создан и настроен"
else
    error "Файл env.production.example не найден"
    exit 1
fi

# Шаг 4: Настройка сервисов
log "Шаг 4: Настройка сервисов..."

# Копируем конфигурации сервисов
cp vpn-bot.service /etc/systemd/system/
cp vpn-bot-telegram.service /etc/systemd/system/

# Обновляем пути в файлах сервисов
sed -i "s|/var/www/vpn-bot|/var/www/vpn-bot|g" /etc/systemd/system/vpn-bot.service
sed -i "s|/var/www/vpn-bot|/var/www/vpn-bot|g" /etc/systemd/system/vpn-bot-telegram.service

# Обновляем systemd
systemctl daemon-reload
systemctl enable vpn-bot vpn-bot-telegram

log "✅ Сервисы настроены"

# Шаг 5: Настройка Nginx
log "Шаг 5: Настройка Nginx..."

# Копируем конфигурацию Nginx
cp nginx.conf /etc/nginx/sites-available/vpn-bot

# Обновляем домен в конфигурации
sed -i "s/your-domain.com/$DOMAIN/g" /etc/nginx/sites-available/vpn-bot

# Активируем сайт
ln -sf /etc/nginx/sites-available/vpn-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверяем конфигурацию
if nginx -t; then
    log "✅ Конфигурация Nginx корректна"
    systemctl restart nginx
else
    error "❌ Ошибка в конфигурации Nginx"
    exit 1
fi

# Шаг 6: Запуск приложения
log "Шаг 6: Запуск приложения..."

# Запускаем деплой
if [ -f "deploy.sh" ]; then
    chmod +x deploy.sh
    ./deploy.sh
else
    error "Файл deploy.sh не найден"
    exit 1
fi

# Шаг 7: Финальная проверка
log "Шаг 7: Финальная проверка..."

# Проверяем статус сервисов
services=("vpn-bot" "vpn-bot-telegram" "nginx" "postgresql" "redis-server")
for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service"; then
        log "✅ $service запущен"
    else
        error "❌ $service не запущен"
    fi
done

# Проверяем порты
if netstat -tlnp | grep -q ":80 "; then
    log "✅ Порт 80 открыт"
else
    warn "⚠️ Порт 80 не открыт"
fi

if netstat -tlnp | grep -q ":8123 "; then
    log "✅ Порт 8123 открыт"
else
    warn "⚠️ Порт 8123 не открыт"
fi

# Финальный отчет
log ""
log "🎉 Деплой завершен успешно!"
log ""
log "📋 Информация о развертывании:"
log "- Домен: $DOMAIN"
log "- Django админка: http://$DOMAIN/admin/"
log "- API: http://$DOMAIN/bot_management/api/"
log "- Telegram бот: @your_bot_username"
log ""
log "🔧 Полезные команды:"
log "- Статус сервисов: systemctl status vpn-bot vpn-bot-telegram nginx"
log "- Логи Django: journalctl -u vpn-bot -f"
log "- Логи бота: journalctl -u vpn-bot-telegram -f"
log "- Логи Nginx: tail -f /var/log/nginx/vpn-bot_error.log"
log "- Перезапуск: systemctl restart vpn-bot vpn-bot-telegram"
log ""
log "🔒 Следующие шаги:"
log "1. Настройте SSL сертификат: certbot --nginx -d $DOMAIN"
log "2. Проверьте работу бота: отправьте /start боту"
log "3. Проверьте платежи через ЮKassa"
log "4. Настройте мониторинг и бэкапы"
log ""
log "📁 Важные файлы:"
log "- Конфигурация: /var/www/vpn-bot/.env"
log "- Логи: /var/www/vpn-bot/logs/"
log "- База данных: /var/www/vpn-bot/database_config.txt"
log ""
log "⚠️ Не забудьте:"
log "- Регулярно обновлять систему"
log "- Делать бэкапы базы данных"
log "- Мониторить логи на предмет ошибок"
log "- Использовать HTTPS в продакшене"
