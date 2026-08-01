# 🐳 Docker для Telegram VPN бота

## Быстрый старт

### 1. Настройка переменных окружения

Скопируйте файл с примером и заполните своими данными:

```bash
cp bot.env.example bot.env
```

Отредактируйте `bot.env` и укажите:
- `BOT_TOKEN` - токен вашего Telegram бота
- `ADMIN_IDS` - ID администраторов (через запятую)
- `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` - данные ЮKassa
- `REMNAWAVE_*` - настройки Remnawave API
- `SECRET_KEY` - секретный ключ Django

### 2. Запуск бота

```bash
# Сборка и запуск
docker-compose up -d --build

# Просмотр логов
docker-compose logs -f bot

# Остановка
docker-compose down
```

### 3. Доступ к сервисам

- **Telegram бот**: работает автоматически
- **Django админка**: http://localhost:8123/admin/
  - Логин: `admin`
  - Пароль: `admin123` (смените после первого входа!)

## Управление

### Просмотр логов
```bash
docker-compose logs -f bot
```

### Перезапуск
```bash
docker-compose restart bot
```

### Обновление
```bash
git pull
docker-compose down
docker-compose up -d --build
```

### Доступ к базе данных
База данных хранится в Docker volume `telegram-bot-data`.

Для экспорта:
```bash
docker run --rm \
  -v telegram-bot-data:/source \
  -v $(pwd):/backup \
  alpine tar czf /backup/db-backup.tar.gz -C /source .
```

### Резервное копирование
```bash
# Создать бэкап базы данных
docker-compose exec bot python manage.py dumpdata > backup.json

# Или скопировать файл БД
docker cp telegram-vpn-bot:/app/data/tg_key_bot.db ./backup.db
```

## Структура файлов

```
.
├── Dockerfile              # Образ Docker
├── docker-compose.yml      # Конфигурация Docker Compose
├── bot.env.example         # Пример переменных окружения
├── bot.env                 # Ваши переменные (не коммитить!)
├── .dockerignore           # Исключения для Docker
└── ...                     # Файлы проекта
```

## Примечания

- База данных SQLite хранится в Docker volume и сохраняется при перезапуске
- Для production рекомендуется использовать PostgreSQL вместо SQLite
- Порт 8123 открыт для доступа к Django админке
- Все логи сохраняются в volume `telegram-bot-logs`

## Troubleshooting

### Бот не запускается
```bash
# Проверьте логи
docker-compose logs bot

# Убедитесь, что bot.env настроен правильно
cat bot.env
```

### Ошибка подключения к базе данных
```bash
# Проверьте права доступа к volume
docker volume inspect telegram-bot-data
```

### Сброс состояния (удалит все данные!)
```bash
docker-compose down -v
docker-compose up -d --build
```
