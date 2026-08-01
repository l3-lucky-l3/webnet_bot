#!/bin/bash

# Скрипт настройки базы данных для VPN бота
# Версия: 1.0

set -e

echo "🗄️ Настройка базы данных для VPN бота..."

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

# Проверяем, что PostgreSQL запущен
if ! systemctl is-active --quiet postgresql; then
    error "PostgreSQL не запущен. Запустите: sudo systemctl start postgresql"
    exit 1
fi

# Параметры базы данных (можно изменить)
DB_NAME="vpn_bot"
DB_USER="vpn_user"
DB_PASSWORD=""

# Генерируем пароль если не указан
if [ -z "$DB_PASSWORD" ]; then
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
    log "Сгенерирован пароль для базы данных: $DB_PASSWORD"
fi

log "Настраиваем базу данных: $DB_NAME"
log "Пользователь: $DB_USER"

# Создаем базу данных и пользователя
log "Создаем базу данных и пользователя..."

sudo -u postgres psql << EOF
-- Создаем пользователя
CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';

-- Создаем базу данных
CREATE DATABASE $DB_NAME OWNER $DB_USER;

-- Предоставляем права
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;

-- Подключаемся к базе и предоставляем права на схему
\c $DB_NAME;
GRANT ALL ON SCHEMA public TO $DB_USER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $DB_USER;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;

-- Настраиваем права по умолчанию для новых таблиц
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $DB_USER;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $DB_USER;

\q
EOF

# Проверяем подключение
log "Проверяем подключение к базе данных..."
if sudo -u postgres psql -d "$DB_NAME" -c "SELECT 1;" > /dev/null 2>&1; then
    log "✅ Подключение к базе данных успешно"
else
    error "❌ Не удалось подключиться к базе данных"
    exit 1
fi

# Настраиваем PostgreSQL для внешних подключений
log "Настраиваем PostgreSQL..."

# Находим файл конфигурации
PG_CONFIG_FILE=$(sudo -u postgres psql -t -c "SHOW config_file;" | xargs)

if [ -f "$PG_CONFIG_FILE" ]; then
    log "Файл конфигурации PostgreSQL: $PG_CONFIG_FILE"
    
    # Создаем бэкап
    cp "$PG_CONFIG_FILE" "${PG_CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Настраиваем подключения
    sudo -u postgres psql -c "ALTER SYSTEM SET listen_addresses = 'localhost';"
    sudo -u postgres psql -c "ALTER SYSTEM SET max_connections = 100;"
    sudo -u postgres psql -c "ALTER SYSTEM SET shared_buffers = '256MB';"
    sudo -u postgres psql -c "ALTER SYSTEM SET effective_cache_size = '1GB';"
    sudo -u postgres psql -c "ALTER SYSTEM SET maintenance_work_mem = '64MB';"
    sudo -u postgres psql -c "ALTER SYSTEM SET checkpoint_completion_target = 0.9;"
    sudo -u postgres psql -c "ALTER SYSTEM SET wal_buffers = '16MB';"
    sudo -u postgres psql -c "ALTER SYSTEM SET default_statistics_target = 100;"
    
    # Перезапускаем PostgreSQL
    systemctl restart postgresql
    
    log "✅ PostgreSQL перезапущен с новыми настройками"
else
    warn "Не удалось найти файл конфигурации PostgreSQL"
fi

# Создаем файл с параметрами подключения
log "Создаем файл с параметрами подключения..."
cat > /var/www/vpn-bot/database_config.txt << EOF
# Параметры подключения к базе данных
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=localhost
DB_PORT=5432

# Строка подключения для Django
DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME
EOF

chown www-data:www-data /var/www/vpn-bot/database_config.txt
chmod 600 /var/www/vpn-bot/database_config.txt

# Создаем скрипт для бэкапа
log "Создаем скрипт для бэкапа..."
cat > /usr/local/bin/backup-vpn-bot-db.sh << 'EOF'
#!/bin/bash
# Скрипт резервного копирования базы данных VPN бота

BACKUP_DIR="/var/backups/vpn-bot"
DB_NAME="vpn_bot"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Создаем директорию для бэкапов
mkdir -p "$BACKUP_DIR"

# Создаем бэкап
sudo -u postgres pg_dump "$DB_NAME" | gzip > "$BACKUP_DIR/vpn_bot_$TIMESTAMP.sql.gz"

# Удаляем старые бэкапы (старше 30 дней)
find "$BACKUP_DIR" -name "vpn_bot_*.sql.gz" -mtime +30 -delete

echo "Бэкап создан: $BACKUP_DIR/vpn_bot_$TIMESTAMP.sql.gz"
EOF

chmod +x /usr/local/bin/backup-vpn-bot-db.sh

# Создаем скрипт для восстановления
log "Создаем скрипт для восстановления..."
cat > /usr/local/bin/restore-vpn-bot-db.sh << 'EOF'
#!/bin/bash
# Скрипт восстановления базы данных VPN бота

if [ $# -eq 0 ]; then
    echo "Использование: $0 <путь_к_файлу_бэкапа>"
    echo "Пример: $0 /var/backups/vpn-bot/vpn_bot_20240101_120000.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"
DB_NAME="vpn_bot"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Файл бэкапа не найден: $BACKUP_FILE"
    exit 1
fi

echo "Восстанавливаем базу данных из файла: $BACKUP_FILE"
echo "ВНИМАНИЕ: Это действие удалит все текущие данные!"
read -p "Продолжить? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Отменено"
    exit 1
fi

# Останавливаем приложение
systemctl stop vpn-bot vpn-bot-telegram

# Восстанавливаем базу данных
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" | sudo -u postgres psql "$DB_NAME"
else
    sudo -u postgres psql "$DB_NAME" < "$BACKUP_FILE"
fi

# Запускаем приложение
systemctl start vpn-bot vpn-bot-telegram

echo "Восстановление завершено"
EOF

chmod +x /usr/local/bin/restore-vpn-bot-db.sh

# Настраиваем автоматические бэкапы
log "Настраиваем автоматические бэкапы..."
(crontab -l 2>/dev/null; echo "0 2 * * * /usr/local/bin/backup-vpn-bot-db.sh") | crontab -

# Проверяем финальную настройку
log "Проверяем финальную настройку..."
if sudo -u postgres psql -d "$DB_NAME" -c "SELECT current_database(), current_user;" > /dev/null 2>&1; then
    log "✅ База данных настроена корректно"
else
    error "❌ Проблемы с настройкой базы данных"
    exit 1
fi

log "✅ Настройка базы данных завершена!"
log ""
log "📋 Информация о базе данных:"
log "- Имя базы: $DB_NAME"
log "- Пользователь: $DB_USER"
log "- Пароль: $DB_PASSWORD"
log "- Хост: localhost"
log "- Порт: 5432"
log ""
log "📁 Файлы:"
log "- Конфигурация: /var/www/vpn-bot/database_config.txt"
log "- Скрипт бэкапа: /usr/local/bin/backup-vpn-bot-db.sh"
log "- Скрипт восстановления: /usr/local/bin/restore-vpn-bot-db.sh"
log ""
log "🔧 Полезные команды:"
log "- Подключение к БД: sudo -u postgres psql $DB_NAME"
log "- Создать бэкап: /usr/local/bin/backup-vpn-bot-db.sh"
log "- Восстановить БД: /usr/local/bin/restore-vpn-bot-db.sh <файл>"
log "- Статус PostgreSQL: systemctl status postgresql"
