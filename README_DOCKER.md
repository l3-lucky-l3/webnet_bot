# 🐳 Docker для Telegram VPN бота

## Варианты использования

### 1. Разработка (Dev) - с прокси/VPN

Используйте этот вариант, если вы разрабатываете локально и у вас включен VPN или прокси.

#### Быстрый старт (Dev)

```bash
# Запуск с прокси (Windows PowerShell)
$env:DOCKER_BUILDKIT=0; docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build

# Просмотр логов
docker compose -f docker-compose.dev.yml logs -f

# Остановка
docker compose -f docker-compose.dev.yml down
```

**Доступ:**
- Django Admin: http://localhost:8123/admin/
  - Логин: `admin`
  - Пароль: `admin123`
- Webhook endpoint: http://localhost:8024/webhook

---

### 2. Продакшен (Prod) - без прокси, с nginx

Используйте этот вариант для развертывания на сервере (например, 188.215.229.165).

#### Быстрый старт (Prod)

```bash
# 1. Отредактируйте .env.prod и замените все значения на реальные
# 2. Убедитесь, что порты 80 и 443 свободны

# Запуск
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# Просмотр логов
docker compose -f docker-compose.prod.yml logs -f

# Остановка
docker compose -f docker-compose.prod.yml down
```

**Доступ:**
- Django Admin: http://188.215.229.165/admin/
- Webhook endpoint: http://188.215.229.165/webhook

---

## Тестирование вебхука (Postman)

### Platega
- **URL:** `http://localhost:8024/webhook` (dev) или `http://188.215.229.165/webhook` (prod)
- **Method:** POST
- **Headers:** `Content-Type: application/json`
- **Body:**
```json
{
  "id": "TEST_PLATEGA_999",
  "order_id": "ORDER_12345",
  "status": "CONFIRMED",
  "amount": 199.00,
  "currency": "RUB"
}
```

### Antilopay
- **URL:** `http://localhost:8024/webhook` (dev) или `http://188.215.229.165/webhook` (prod)
- **Method:** POST
- **Headers:** 
  - `Content-Type: application/json`
  - `X-Apay-Callback: fake_signature_for_testing`
- **Body:**
```json
{
  "payment_id": "APAY_TEST_888",
  "order_id": "ORDER_ANTILO_555",
  "status": "SUCCESS",
  "amount": 299.00,
  "currency": "RUB"
}
```

---

## Структура файлов

```
.
├── Dockerfile.dev          # Образ для разработки (с прокси)
├── Dockerfile.prod         # Образ для продакшена (без прокси)
├── docker-compose.dev.yml  # Конфигурация для разработки
├── docker-compose.prod.yml # Конфигурация для продакшена (с nginx)
├── .env.dev                # Переменные окружения для разработки
├── .env.prod               # Переменные окружения для продакшена
├── nginx/
│   └── nginx.conf          # Конфигурация nginx для продакшена
└── ...                     # Файлы проекта
```

## Примечания

- Для production рекомендуется использовать PostgreSQL вместо SQLite
- Nginx в production конфигурации автоматически проксирует запросы на Django и webhook сервер
- Все логи сохраняются в Docker volumes
