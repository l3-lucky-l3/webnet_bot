#!/bin/bash

# Скрипт настройки SSL для VPN бота
# Версия: 1.0

set -e

echo "🔒 Настройка SSL для VPN бота..."

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
EMAIL=""

# Запрашиваем параметры
if [ -z "$DOMAIN" ]; then
    read -p "Введите ваш домен (например: example.com): " DOMAIN
fi

if [ -z "$EMAIL" ]; then
    read -p "Введите ваш email для Let's Encrypt: " EMAIL
fi

log "Настраиваем SSL для домена: $DOMAIN"
log "Email: $EMAIL"

# Проверяем, что домен указывает на сервер
log "Проверяем DNS запись..."
SERVER_IP=$(curl -s ifconfig.me)
DOMAIN_IP=$(dig +short $DOMAIN | tail -n1)

if [ "$SERVER_IP" = "$DOMAIN_IP" ]; then
    log "✅ DNS запись настроена корректно"
else
    warn "⚠️ DNS запись может быть настроена неправильно"
    warn "Сервер IP: $SERVER_IP"
    warn "Домен IP: $DOMAIN_IP"
    read -p "Продолжить? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Отменено"
        exit 1
    fi
fi

# Проверяем, что Nginx запущен
if ! systemctl is-active --quiet nginx; then
    error "Nginx не запущен. Запустите: sudo systemctl start nginx"
    exit 1
fi

# Проверяем, что сайт доступен по HTTP
log "Проверяем доступность сайта по HTTP..."
if curl -s -o /dev/null -w "%{http_code}" "http://$DOMAIN" | grep -q "200\|301\|302"; then
    log "✅ Сайт доступен по HTTP"
else
    warn "⚠️ Сайт недоступен по HTTP. Проверьте конфигурацию Nginx"
fi

# Устанавливаем Certbot если не установлен
if ! command -v certbot &> /dev/null; then
    log "Устанавливаем Certbot..."
    apt update
    apt install -y certbot python3-certbot-nginx
fi

# Получаем SSL сертификат
log "Получаем SSL сертификат..."
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --email "$EMAIL" --agree-tos --non-interactive --redirect

# Проверяем получение сертификата
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    log "✅ SSL сертификат получен успешно"
else
    error "❌ Не удалось получить SSL сертификат"
    exit 1
fi

# Настраиваем автообновление сертификата
log "Настраиваем автообновление сертификата..."
(crontab -l 2>/dev/null; echo "0 12 * * * /usr/bin/certbot renew --quiet") | crontab -

# Проверяем автообновление
log "Проверяем автообновление сертификата..."
if certbot renew --dry-run; then
    log "✅ Автообновление настроено корректно"
else
    warn "⚠️ Проблемы с автообновлением сертификата"
fi

# Перезапускаем Nginx
log "Перезапускаем Nginx..."
systemctl restart nginx

# Проверяем HTTPS
log "Проверяем HTTPS..."
if curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN" | grep -q "200"; then
    log "✅ HTTPS работает корректно"
else
    warn "⚠️ Проблемы с HTTPS"
fi

# Проверяем редирект с HTTP на HTTPS
log "Проверяем редирект с HTTP на HTTPS..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://$DOMAIN")
if [ "$HTTP_CODE" = "301" ] || [ "$HTTP_CODE" = "302" ]; then
    log "✅ Редирект с HTTP на HTTPS работает"
else
    warn "⚠️ Редирект с HTTP на HTTPS не работает"
fi

# Проверяем SSL рейтинг
log "Проверяем SSL рейтинг..."
SSL_RATING=$(curl -s "https://api.ssllabs.com/api/v3/analyze?host=$DOMAIN" | grep -o '"grade":"[A-F]"' | cut -d'"' -f4 || echo "Недоступно")
if [ "$SSL_RATING" != "Недоступно" ]; then
    log "SSL рейтинг: $SSL_RATING"
else
    warn "Не удалось получить SSL рейтинг"
fi

# Настраиваем дополнительные заголовки безопасности
log "Настраиваем дополнительные заголовки безопасности..."

# Создаем конфигурацию для дополнительных заголовков
cat > /etc/nginx/snippets/ssl-security.conf << 'EOF'
# Дополнительные заголовки безопасности для HTTPS
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "no-referrer-when-downgrade" always;
add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
EOF

# Обновляем конфигурацию Nginx
NGINX_CONFIG="/etc/nginx/sites-available/vpn-bot"
if [ -f "$NGINX_CONFIG" ]; then
    # Добавляем подключение дополнительных заголовков
    if ! grep -q "ssl-security.conf" "$NGINX_CONFIG"; then
        sed -i '/server_name/a\    include /etc/nginx/snippets/ssl-security.conf;' "$NGINX_CONFIG"
    fi
    
    # Проверяем конфигурацию
    if nginx -t; then
        log "✅ Конфигурация Nginx обновлена"
        systemctl reload nginx
    else
        warn "⚠️ Ошибка в конфигурации Nginx"
    fi
fi

# Настраиваем мониторинг SSL сертификата
log "Настраиваем мониторинг SSL сертификата..."
cat > /usr/local/bin/check-ssl-cert.sh << EOF
#!/bin/bash
# Скрипт проверки SSL сертификата

DOMAIN="$DOMAIN"
DAYS_THRESHOLD=30

# Проверяем срок действия сертификата
CERT_DAYS=\$(openssl s_client -connect \$DOMAIN:443 -servername \$DOMAIN 2>/dev/null | openssl x509 -noout -dates | grep notAfter | cut -d= -f2 | xargs -I {} date -d {} +%s)
CURRENT_DAYS=\$(date +%s)
DAYS_LEFT=\$(((\$CERT_DAYS - \$CURRENT_DAYS) / 86400))

if [ \$DAYS_LEFT -lt \$DAYS_THRESHOLD ]; then
    echo "⚠️ SSL сертификат для \$DOMAIN истекает через \$DAYS_LEFT дней"
    # Можно добавить отправку уведомления
fi
EOF

chmod +x /usr/local/bin/check-ssl-cert.sh

# Добавляем проверку в cron
(crontab -l 2>/dev/null; echo "0 9 * * * /usr/local/bin/check-ssl-cert.sh") | crontab -

# Финальная проверка
log "Финальная проверка SSL..."

# Проверяем статус сертификата
CERT_INFO=$(openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -dates)
log "Информация о сертификате:"
echo "$CERT_INFO"

# Проверяем доступность HTTPS
if curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN" | grep -q "200"; then
    log "✅ HTTPS работает корректно"
else
    error "❌ Проблемы с HTTPS"
fi

log ""
log "🔒 SSL настройка завершена успешно!"
log ""
log "📋 Информация:"
log "- Домен: $DOMAIN"
log "- HTTPS: https://$DOMAIN"
log "- Сертификат: /etc/letsencrypt/live/$DOMAIN/"
log "- Автообновление: настроено"
log "- Мониторинг: настроен"
log ""
log "🔧 Полезные команды:"
log "- Проверка сертификата: certbot certificates"
log "- Обновление сертификата: certbot renew"
log "- Проверка SSL: /usr/local/bin/check-ssl-cert.sh"
log "- Логи Certbot: journalctl -u certbot.timer -f"
log ""
log "⚠️ Важно:"
log "- Сертификат будет автоматически обновляться"
log "- Мониторинг SSL настроен"
log "- Дополнительные заголовки безопасности добавлены"
