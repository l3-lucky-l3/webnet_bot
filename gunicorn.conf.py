# Gunicorn конфигурация для VPN бота

import os
import multiprocessing

# Основные настройки
bind = "0.0.0.0:8123"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

# Логирование
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Процессы
max_requests = 1000
max_requests_jitter = 50
preload_app = True

# Безопасность
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Переменные окружения
raw_env = [
    'DJANGO_SETTINGS_MODULE=tg_bot_admin.settings_production',
]

