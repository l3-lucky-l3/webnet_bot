#!/bin/bash
# Скрипт для автоматической настройки Caddy для Platega callback

set -e

echo "🔧 Настройка Caddy для Platega callback"
echo ""

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Запустите скрипт с sudo: sudo ./setup_caddy.sh"
    exit 1
fi

# Запрос домена
read -p "Введите поддомен для callback (например: callback): " SUBDOMAIN
read -p "Введите ваш домен (например: yourdomain.com): " DOMAIN

FULL_DOMAIN="${SUBDOMAIN}.${DOMAIN}"

echo ""
echo "📋 Будет настроен: https://${FULL_DOMAIN}/api/platega/callback/"
echo ""
read -p "Продолжить? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Отменено"
    exit 1
fi

# Установка Caddy
echo ""
echo "📥 Установка Caddy..."

if ! command -v caddy &> /dev/null; then
    # Установка зависимостей
    apt update
    apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    
    # Добавление репозитория Caddy
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    
    # Установка
    apt update
    apt install -y caddy
    
    echo "✅ Caddy установлен"
else
    echo "✅ Caddy уже установлен"
fi

# Создание конфигурации
echo ""
echo "📝 Создание конфигурации Caddy..."

CADDYFILE="/etc/caddy/Caddyfile"

# Проверяем, существует ли уже конфигурация для этого домена
if grep -q "${FULL_DOMAIN}" "${CADDYFILE}" 2>/dev/null; then
    echo "⚠️  Конфигурация для ${FULL_DOMAIN} уже существует"
    read -p "Перезаписать? (y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Отменено"
        exit 1
    fi
    # Удаляем старую конфигурацию
    sed -i "/${FULL_DOMAIN}/,/^}/d" "${CADDYFILE}"
fi

# Добавляем конфигурацию
cat >> "${CADDYFILE}" << EOF

# Platega callback endpoint
${FULL_DOMAIN} {
    reverse_proxy localhost:8018 {
        header_up Host {host}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
        
        # Важно для Platega callback (таймаут 60 секунд)
        transport http {
            dial_timeout 60s
            response_header_timeout 60s
        }
    }
    
    # Логирование
    log {
        output file /var/log/caddy/platega-callback.log
        format json
    }
}
EOF

echo "✅ Конфигурация создана"

# Настройка DNS (информация)
echo ""
echo "📋 ВАЖНО: Настройте DNS запись!"
echo ""
echo "Создайте A запись в DNS вашего домена:"
echo "  Имя: ${SUBDOMAIN}"
echo "  Тип: A"
echo "  Значение: $(curl -s ifconfig.me)"  # Публичный IP сервера
echo ""
echo "Или если домен уже указывает на этот сервер, создайте CNAME:"
echo "  Имя: ${SUBDOMAIN}"
echo "  Тип: CNAME"
echo "  Значение: ${DOMAIN}"
echo ""
read -p "Нажмите Enter после настройки DNS..."

# Проверка DNS
echo ""
echo "🔍 Проверка DNS..."
if dig +short "${FULL_DOMAIN}" | grep -q .; then
    echo "✅ DNS запись найдена"
else
    echo "⚠️  DNS запись не найдена. Убедитесь, что она настроена правильно."
    echo "Продолжить все равно? (y/n): "
    read -p "" -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Отменено"
        exit 1
    fi
fi

# Создание директории для логов
mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy

# Тестирование конфигурации
echo ""
echo "🔍 Проверка конфигурации Caddy..."
if caddy validate --config "${CADDYFILE}"; then
    echo "✅ Конфигурация валидна"
else
    echo "❌ Ошибка в конфигурации"
    exit 1
fi

# Перезапуск Caddy
echo ""
echo "🔄 Перезапуск Caddy..."
systemctl restart caddy
systemctl enable caddy

# Проверка статуса
sleep 2
if systemctl is-active --quiet caddy; then
    echo "✅ Caddy запущен"
else
    echo "❌ Ошибка запуска Caddy"
    systemctl status caddy
    exit 1
fi

# Итоги
echo ""
echo "✅ Настройка завершена!"
echo ""
echo "📋 Итоговая информация:"
echo "  Домен: https://${FULL_DOMAIN}"
echo "  Endpoint: https://${FULL_DOMAIN}/api/platega/callback/"
echo ""
echo "📝 Укажите этот URL в Platega:"
echo "  Настройки → Callback URLs → https://${FULL_DOMAIN}/api/platega/callback/"
echo ""
echo "🔍 Проверка работы:"
echo "  sudo systemctl status caddy"
echo "  sudo tail -f /var/log/caddy/platega-callback.log"
echo ""

