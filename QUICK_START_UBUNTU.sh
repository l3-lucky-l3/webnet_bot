#!/bin/bash

# Быстрый запуск бота и админки на Ubuntu
# Использование: ./QUICK_START_UBUNTU.sh

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR" || exit 1

echo -e "${GREEN}=== Запуск VPN бота и админки ===${NC}\n"

# Проверка виртуального окружения
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Создание виртуального окружения...${NC}"
    python3 -m venv venv
fi

# Активация виртуального окружения
source venv/bin/activate

# Проверка .env файла
if [ ! -f ".env" ]; then
    echo -e "${RED}ОШИБКА: Файл .env не найден!${NC}"
    echo "Создайте файл .env на основе env.production.example"
    exit 1
fi

echo -e "${GREEN}Выберите способ запуска:${NC}"
echo "1) Systemd (рекомендуется для production)"
echo "2) Screen (простой способ)"
echo "3) Tmux (альтернатива screen)"
echo "4) PM2 (требует Node.js)"
echo "5) Ручной запуск в терминале"
echo ""
read -p "Ваш выбор (1-5): " choice

case $choice in
    1)
        echo -e "${GREEN}Настройка systemd...${NC}"
        echo ""
        echo "1. Скопируйте файлы сервисов:"
        echo "   sudo cp vpn-bot.service /etc/systemd/system/"
        echo "   sudo cp vpn-bot-telegram.service /etc/systemd/system/"
        echo ""
        echo "2. Отредактируйте пути в файлах:"
        echo "   sudo nano /etc/systemd/system/vpn-bot.service"
        echo "   sudo nano /etc/systemd/system/vpn-bot-telegram.service"
        echo ""
        echo "3. Запустите сервисы:"
        echo "   sudo systemctl daemon-reload"
        echo "   sudo systemctl enable vpn-bot vpn-bot-telegram"
        echo "   sudo systemctl start vpn-bot vpn-bot-telegram"
        echo ""
        echo "4. Проверьте статус:"
        echo "   sudo systemctl status vpn-bot"
        echo "   sudo systemctl status vpn-bot-telegram"
        echo ""
        echo "5. Логи:"
        echo "   sudo journalctl -u vpn-bot -f"
        echo "   sudo journalctl -u vpn-bot-telegram -f"
        ;;
    2)
        echo -e "${GREEN}Запуск через screen...${NC}"
        
        # Проверка screen
        if ! command -v screen &> /dev/null; then
            echo "Установка screen..."
            sudo apt-get update && sudo apt-get install -y screen
        fi
        
        # Запуск Django
        screen -dmS django bash -c "cd '$BOT_DIR' && source venv/bin/activate && gunicorn --config gunicorn.conf.py wsgi:application"
        echo "✅ Django запущен в screen сессии 'django'"
        
        # Небольшая задержка
        sleep 2
        
        # Запуск бота
        screen -dmS bot bash -c "cd '$BOT_DIR' && source venv/bin/activate && python bot_with_django.py"
        echo "✅ Бот запущен в screen сессии 'bot'"
        
        echo ""
        echo "Управление:"
        echo "  screen -r django  - подключиться к Django"
        echo "  screen -r bot     - подключиться к боту"
        echo "  screen -ls        - список сессий"
        echo "  Ctrl+A, затем D   - отсоединиться (не закрывая)"
        ;;
    3)
        echo -e "${GREEN}Запуск через tmux...${NC}"
        
        # Проверка tmux
        if ! command -v tmux &> /dev/null; then
            echo "Установка tmux..."
            sudo apt-get update && sudo apt-get install -y tmux
        fi
        
        # Создание сессии с двумя окнами
        tmux new-session -d -s vpn-bot -n django "cd '$BOT_DIR' && source venv/bin/activate && gunicorn --config gunicorn.conf.py wsgi:application"
        tmux new-window -t vpn-bot -n bot "cd '$BOT_DIR' && source venv/bin/activate && python bot_with_django.py"
        
        echo "✅ Django и бот запущены в tmux сессии 'vpn-bot'"
        echo ""
        echo "Управление:"
        echo "  tmux attach -t vpn-bot  - подключиться к сессии"
        echo "  Ctrl+B, затем 0,1      - переключение между окнами"
        echo "  Ctrl+B, затем D        - отсоединиться"
        ;;
    4)
        echo -e "${GREEN}Запуск через PM2...${NC}"
        
        # Проверка PM2
        if ! command -v pm2 &> /dev/null; then
            echo "Установка PM2..."
            sudo npm install -g pm2
        fi
        
        # Создание конфигурации
        cat > ecosystem.config.js << EOF
module.exports = {
  apps: [
    {
      name: 'vpn-bot-django',
      script: 'venv/bin/gunicorn',
      args: '--config gunicorn.conf.py wsgi:application',
      cwd: '$BOT_DIR',
      interpreter: 'none',
      env: {
        DJANGO_SETTINGS_MODULE: 'tg_bot_admin.settings_production'
      },
      error_file: 'logs/django-error.log',
      out_file: 'logs/django-out.log',
      autorestart: true
    },
    {
      name: 'vpn-bot-telegram',
      script: 'bot_with_django.py',
      cwd: '$BOT_DIR',
      interpreter: 'venv/bin/python',
      error_file: 'logs/bot-error.log',
      out_file: 'logs/bot-out.log',
      autorestart: true
    }
  ]
};
EOF
        
        pm2 start ecosystem.config.js
        pm2 save
        pm2 startup
        
        echo "✅ Запущено через PM2"
        echo ""
        echo "Управление:"
        echo "  pm2 status           - статус"
        echo "  pm2 logs             - логи"
        echo "  pm2 restart all      - перезапуск"
        echo "  pm2 stop all         - остановка"
        ;;
    5)
        echo -e "${GREEN}Ручной запуск${NC}"
        echo ""
        echo "Откройте ДВА терминала:"
        echo ""
        echo "${YELLOW}Терминал 1 (Django):${NC}"
        echo "  cd $BOT_DIR"
        echo "  source venv/bin/activate"
        echo "  gunicorn --config gunicorn.conf.py wsgi:application"
        echo ""
        echo "${YELLOW}Терминал 2 (Бот):${NC}"
        echo "  cd $BOT_DIR"
        echo "  source venv/bin/activate"
        echo "  python bot_with_django.py"
        echo ""
        echo "Или используйте скрипт:"
        echo "  python run_bot_with_django.py"
        ;;
    *)
        echo -e "${RED}Неверный выбор${NC}"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}=== Готово! ===${NC}"
echo ""
echo "Проверка:"
echo "  - Django админка: http://localhost:8123/admin/"
echo "  - Telegram бот: отправьте /start боту"






