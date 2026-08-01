#!/bin/bash

# Скрипт установки зависимостей для VPN бота
# Версия: 1.0

set -e

echo "🔧 Установка зависимостей для VPN бота..."

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

# Определяем дистрибутив
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
else
    error "Не удалось определить операционную систему"
    exit 1
fi

log "Обнаружена ОС: $OS $VER"

# Обновляем систему
log "Обновляем систему..."
apt update && apt upgrade -y

# Устанавливаем основные пакеты
log "Устанавливаем основные пакеты..."
apt install -y \
    curl \
    wget \
    git \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-setuptools

# Устанавливаем Python 3.10+ если нужно
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
if [ "$(echo "$PYTHON_VERSION < 3.10" | bc -l)" -eq 1 ]; then
    warn "Текущая версия Python: $PYTHON_VERSION. Устанавливаем Python 3.10+..."
    
    # Добавляем репозиторий deadsnakes для Ubuntu
    if [[ "$OS" == *"Ubuntu"* ]]; then
        add-apt-repository ppa:deadsnakes/ppa -y
        apt update
        apt install -y python3.10 python3.10-venv python3.10-dev
        update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
    else
        warn "Автоматическая установка Python 3.10+ не поддерживается для $OS"
        warn "Установите Python 3.10+ вручную"
    fi
else
    log "✅ Python $PYTHON_VERSION уже установлен"
fi

# Устанавливаем PostgreSQL
log "Устанавливаем PostgreSQL..."
apt install -y postgresql postgresql-contrib

# Настраиваем PostgreSQL
log "Настраиваем PostgreSQL..."
systemctl start postgresql
systemctl enable postgresql

# Устанавливаем Redis
log "Устанавливаем Redis..."
apt install -y redis-server

# Настраиваем Redis
log "Настраиваем Redis..."
systemctl start redis-server
systemctl enable redis-server

# Устанавливаем Nginx
log "Устанавливаем Nginx..."
apt install -y nginx

# Настраиваем Nginx
log "Настраиваем Nginx..."
systemctl start nginx
systemctl enable nginx

# Устанавливаем Certbot для SSL
log "Устанавливаем Certbot..."
apt install -y certbot python3-certbot-nginx

# Настраиваем файрвол
log "Настраиваем файрвол..."
ufw --force enable
ufw allow ssh
ufw allow 80
ufw allow 443

# Устанавливаем дополнительные утилиты
log "Устанавливаем дополнительные утилиты..."
apt install -y \
    htop \
    tree \
    nano \
    vim \
    netstat-nat \
    net-tools \
    lsof \
    fail2ban

# Настраиваем fail2ban
log "Настраиваем fail2ban..."
systemctl start fail2ban
systemctl enable fail2ban

# Создаем пользователя для приложения
log "Создаем пользователя для приложения..."
if ! id "www-data" &>/dev/null; then
    useradd -r -s /bin/false www-data
fi

# Создаем директорию для проекта
log "Создаем директорию для проекта..."
mkdir -p /var/www/vpn-bot
chown www-data:www-data /var/www/vpn-bot

# Настраиваем логирование
log "Настраиваем логирование..."
mkdir -p /var/log/vpn-bot
chown www-data:www-data /var/log/vpn-bot

# Проверяем установленные сервисы
log "Проверяем установленные сервисы..."
services=("postgresql" "redis-server" "nginx" "fail2ban")
for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service"; then
        log "✅ $service запущен"
    else
        error "❌ $service не запущен"
    fi
done

# Проверяем порты
log "Проверяем порты..."
if netstat -tlnp | grep -q ":5432 "; then
    log "✅ PostgreSQL (5432) открыт"
else
    warn "⚠️ PostgreSQL (5432) не открыт"
fi

if netstat -tlnp | grep -q ":6379 "; then
    log "✅ Redis (6379) открыт"
else
    warn "⚠️ Redis (6379) не открыт"
fi

if netstat -tlnp | grep -q ":80 "; then
    log "✅ Nginx (80) открыт"
else
    warn "⚠️ Nginx (80) не открыт"
fi

log "✅ Установка зависимостей завершена!"
log ""
log "📋 Следующие шаги:"
log "1. Настройте базу данных PostgreSQL"
log "2. Загрузите проект в /var/www/vpn-bot"
log "3. Настройте переменные окружения"
log "4. Запустите деплой: ./deploy.sh"
log ""
log "🔧 Полезные команды:"
log "- Статус сервисов: systemctl status postgresql redis-server nginx"
log "- Логи PostgreSQL: journalctl -u postgresql -f"
log "- Логи Redis: journalctl -u redis-server -f"
log "- Логи Nginx: journalctl -u nginx -f"
