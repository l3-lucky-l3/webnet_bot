#!/bin/bash
# Скрипт для настройки Cloudflare Tunnel для Platega callback
# Полностью бесплатно и постоянный URL!

set -e

echo "🔧 Настройка Cloudflare Tunnel для Platega callback"
echo ""

# Проверка наличия cloudflared
if ! command -v cloudflared &> /dev/null; then
    echo "📥 Установка cloudflared..."
    
    # Определяем архитектуру
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then
        ARCH="amd64"
    elif [ "$ARCH" = "aarch64" ]; then
        ARCH="arm64"
    else
        echo "❌ Неподдерживаемая архитектура: $ARCH"
        exit 1
    fi
    
    # Скачиваем cloudflared
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH} -O /tmp/cloudflared
    chmod +x /tmp/cloudflared
    sudo mv /tmp/cloudflared /usr/local/bin/cloudflared
    
    echo "✅ cloudflared установлен"
else
    echo "✅ cloudflared уже установлен"
fi

echo ""
echo "📋 Инструкция по настройке:"
echo ""
echo "1. Зарегистрируйтесь на https://cloudflare.com (бесплатно)"
echo "2. Войдите в аккаунт через cloudflared:"
echo "   cloudflared tunnel login"
echo ""
echo "3. Создайте туннель:"
echo "   cloudflared tunnel create platega-callback"
echo ""
echo "4. Настройте маршрут (если есть домен):"
echo "   cloudflared tunnel route dns platega-callback callback.yourdomain.com"
echo ""
echo "5. Или используйте бесплатный поддомен:"
echo "   cloudflared tunnel --url http://localhost:8018"
echo ""
echo "6. Для постоянного запуска создайте systemd service:"
echo ""
echo "Создайте файл /etc/systemd/system/cloudflared-tunnel.service:"
echo ""
cat << 'EOF'
[Unit]
Description=Cloudflare Tunnel for Platega callback
After=network.target

[Service]
Type=simple
User=ваш-пользователь
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8018
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "7. Запустите service:"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable cloudflared-tunnel"
echo "   sudo systemctl start cloudflared-tunnel"
echo ""
echo "8. Проверьте статус:"
echo "   sudo systemctl status cloudflared-tunnel"
echo ""
echo "✅ Готово! Используйте полученный URL в Platega"
echo ""

