import time
import logging
import asyncio
from collections import defaultdict, deque
from django.core.cache import cache
from functools import wraps
from typing import Dict, List

logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    from config import ADMIN_IDS
    result = user_id in ADMIN_IDS
    logger.debug(f"is_admin check: user_id={user_id}, ADMIN_IDS={ADMIN_IDS}, result={result}")
    return result

def get_user_display_name(user) -> str:
    """Получить отображаемое имя пользователя"""
    if not user:
        return "Unknown"

    name_parts = []
    if hasattr(user, 'first_name') and user.first_name:
        name_parts.append(user.first_name)
    if hasattr(user, 'last_name') and user.last_name:
        name_parts.append(user.last_name)

    display_name = " ".join(name_parts) if name_parts else "NoName"
    if hasattr(user, 'username') and user.username:
        display_name += f" (@{user.username})"

    return display_name

class TelegramThrottler:
    """Throttling для запросов к Telegram API"""

    def __init__(self, max_concurrent=20, requests_per_second=25):
        self.max_concurrent = max_concurrent  # Максимум одновременных запросов
        self.requests_per_second = requests_per_second  # Максимум запросов в секунду
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.request_times: deque = deque(maxlen=requests_per_second * 10)
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Получить разрешение на запрос"""
        await self.semaphore.acquire()

        async with self._lock:
            current_time = time.time()

            # Удаляем старые записи (старше 1 секунды)
            while self.request_times and current_time - self.request_times[0] > 1:
                self.request_times.popleft()

            # Если превышен лимит в секунду, ждем
            if len(self.request_times) >= self.requests_per_second:
                sleep_time = 1 - (current_time - self.request_times[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            self.request_times.append(time.time())

    def release(self):
        """Освободить разрешение"""
        self.semaphore.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()


class BotSecurity:
    """Система безопасности для Telegram бота"""

    def __init__(self):
        # Лимиты для пользователей
        self.user_limits = {
            'messages_per_minute': 15,      # 15 сообщений в минуту
            'callbacks_per_minute': 30,     # 30 нажатий кнопок в минуту
            'support_messages_per_hour': 10, # 10 сообщений в поддержку в час
            'payment_attempts_per_hour': 5, # 5 попыток оплаты в час
        }
        
        # Заблокированные пользователи (теперь только через кеш)
        
        # Подозрительные паттерны в сообщениях
        self.spam_patterns = [
            'реклама'
        ]
        
        # Паттерны SQL injection
        self.sql_injection_patterns = [
            'union select', 'drop table', 'delete from', 'insert into',
            'update set', 'exec', 'execute', '--', ';--', '/*', '*/',
            "' or '1'='1", "' or 1=1", "admin'--", "1' or '1'='1"
        ]
        
        # Паттерны XSS
        self.xss_patterns = [
            '<script', '</script>', '<iframe', 'javascript:', 'onerror=',
            'onload=', '<img', 'onclick=', 'onmouseover=', 'eval(',
            'document.cookie', 'window.location', 'alert('
        ]
    
    def is_user_blocked(self, user_id):
        """Проверка, заблокирован ли пользователь"""
        # Проверяем только кеш - он автоматически истекает через 60 секунд
        return cache.get(f"blocked_user_{user_id}", False)

    def is_block_notified(self, user_id):
        """Проверка, было ли отправлено уведомление о блокировке"""
        return cache.get(f"block_notified_{user_id}", False)

    def set_block_notified(self, user_id):
        """Установка флага уведомления о блокировке"""
        cache.set(f"block_notified_{user_id}", True, timeout=60)  # На время блокировки
    
    def block_user(self, user_id, reason="Spam detected"):
        """Блокировка пользователя"""
        cache.set(f"blocked_user_{user_id}", True, timeout=60)  # Блокировка на минуту
        # Сбрасываем флаг уведомления о блокировке, чтобы отправить новое
        cache.delete(f"block_notified_{user_id}")
        logger.warning(f"Пользователь {user_id} заблокирован: {reason}")
    
    def check_message_rate_limit(self, user_id):
        """Проверка лимита сообщений"""
        # Возвращает (is_blocked, should_notify)
        if self.is_user_blocked(user_id):
            # Пользователь заблокирован - проверяем, уведомляли ли уже
            should_notify = not self.is_block_notified(user_id)
            if should_notify:
                self.set_block_notified(user_id)
            return True, should_notify

        cache_key = f"messages_{user_id}"
        current_time = time.time()

        # Получаем сообщения за последнюю минуту
        messages = cache.get(cache_key, [])
        messages = [msg_time for msg_time in messages if current_time - msg_time < 60]

        if len(messages) >= self.user_limits['messages_per_minute']:
            logger.info(f"🚫 Пользователь {user_id} превысил лимит сообщений ({len(messages)}/{self.user_limits['messages_per_minute']})")
            self.block_user(user_id, "Message rate limit exceeded")
            return True, True  # Только что заблокировали - нужно уведомить

        # Добавляем текущее сообщение
        messages.append(current_time)
        cache.set(cache_key, messages, timeout=60)

        return False, False
    
    def check_callback_rate_limit(self, user_id):
        """Проверка лимита нажатий кнопок"""
        # Возвращает (is_blocked, should_notify)
        if self.is_user_blocked(user_id):
            # Пользователь заблокирован - проверяем, уведомляли ли уже
            should_notify = not self.is_block_notified(user_id)
            if should_notify:
                self.set_block_notified(user_id)
            return True, should_notify

        cache_key = f"callbacks_{user_id}"
        current_time = time.time()

        # Получаем нажатия за последнюю минуту
        callbacks = cache.get(cache_key, [])
        callbacks = [cb_time for cb_time in callbacks if current_time - cb_time < 60]

        if len(callbacks) >= self.user_limits['callbacks_per_minute']:
            logger.info(f"🚫 Пользователь {user_id} превысил лимит callback'ов ({len(callbacks)}/{self.user_limits['callbacks_per_minute']})")
            self.block_user(user_id, "Callback rate limit exceeded")
            return True, True  # Только что заблокировали - нужно уведомить

        # Добавляем текущее нажатие
        callbacks.append(current_time)
        cache.set(cache_key, callbacks, timeout=60)

        return False, False
    
    def check_support_rate_limit(self, user_id):
        """Проверка лимита сообщений в поддержку"""
        if self.is_user_blocked(user_id):
            return True
        
        cache_key = f"support_{user_id}"
        current_time = time.time()
        
        # Получаем сообщения за последний час
        messages = cache.get(cache_key, [])
        messages = [msg_time for msg_time in messages if current_time - msg_time < 3600]
        
        if len(messages) >= self.user_limits['support_messages_per_hour']:
            self.block_user(user_id, "Support rate limit exceeded")
            return True
        
        # Добавляем текущее сообщение
        messages.append(current_time)
        cache.set(cache_key, messages, timeout=3600)
        
        return False
    
    def check_payment_rate_limit(self, user_id):
        """Проверка лимита попыток оплаты"""
        if self.is_user_blocked(user_id):
            return True
        
        cache_key = f"payments_{user_id}"
        current_time = time.time()
        
        # Получаем попытки за последний час
        payments = cache.get(cache_key, [])
        payments = [pay_time for pay_time in payments if current_time - pay_time < 3600]
        
        if len(payments) >= self.user_limits['payment_attempts_per_hour']:
            self.block_user(user_id, "Payment rate limit exceeded")
            return True
        
        # Добавляем текущую попытку
        payments.append(current_time)
        cache.set(cache_key, payments, timeout=3600)
        
        return False
    
    def is_spam_message(self, text):
        """Проверка на спам в сообщении"""
        if not text:
            return False
        
        text_lower = text.lower()
        
        # Проверяем на спам паттерны
        spam_count = sum(1 for pattern in self.spam_patterns if pattern in text_lower)
        if spam_count >= 2:  # Если найдено 2+ спам слова
            return True
        
        # Проверяем на повторяющиеся символы
        if len(set(text)) < len(text) * 0.3:  # Если уникальных символов меньше 30%
            return True
        
        # Проверяем на длинные сообщения (возможный спам)
        if len(text) > 1000:
            return True
        
        return False
    
    def validate_telegram_data(self, data):
        """Валидация данных от Telegram"""
        required_fields = ['message', 'from']
        
        if not isinstance(data, dict):
            return False, "Invalid data format"
        
        for field in required_fields:
            if field not in data:
                return False, f"Missing required field: {field}"
        
        # Проверяем структуру сообщения
        message = data.get('message', {})
        if not isinstance(message, dict):
            return False, "Invalid message format"
        
        # Проверяем пользователя
        user = message.get('from', {})
        if not isinstance(user, dict) or 'id' not in user:
            return False, "Invalid user data"
        
        return True, None

# Глобальные экземпляры
bot_security = BotSecurity()
telegram_throttler = TelegramThrottler(max_concurrent=15, requests_per_second=20)

def rate_limit_check(limit_type='messages'):
    """Декоратор для проверки лимитов в боте"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Извлекаем user_id из аргументов
            user_id = None
            for arg in args:
                if hasattr(arg, 'from_user') and hasattr(arg.from_user, 'id'):
                    user_id = arg.from_user.id
                    break
                elif hasattr(arg, 'user_id'):
                    user_id = arg.user_id
                    break

            logger.debug(f"Rate limit check for {func.__name__}, user_id={user_id}, limit_type={limit_type}")

            if not user_id:
                return await func(*args, **kwargs)

            # Пропускаем все проверки для админов
            if is_admin(user_id):
                # Агрессивно сбрасываем ВСЕ возможные блокировки и лимиты для админа
                cache_keys = [
                    f"blocked_user_{user_id}",
                    f"block_notified_{user_id}",
                    f"messages_{user_id}",
                    f"callbacks_{user_id}",
                    f"support_{user_id}",
                    f"payments_{user_id}",
                    f"spam_protection_{user_id}",
                    f"user_spam_{user_id}",
                ]
                for key in cache_keys:
                    cache.delete(key)

                # Получаем имя пользователя для логирования
                user = None
                for arg in args:
                    if hasattr(arg, 'from_user') and arg.from_user:
                        user = arg.from_user
                        break

                display_name = get_user_display_name(user) if user else "Unknown"
                logger.info(f"👑 Админ {display_name} (ID: {user_id}) использует {func.__name__} - полный сброс лимитов и пропуск проверок")
                return await func(*args, **kwargs)

            # Проверяем блокировку
            if bot_security.is_user_blocked(user_id):
                logger.warning(f"Заблокированный пользователь {user_id} пытается использовать {func.__name__}")
                return
            
            # Проверяем лимиты
            if limit_type == 'messages':
                is_blocked, _ = bot_security.check_message_rate_limit(user_id)
                if is_blocked:
                    return
            elif limit_type == 'callbacks':
                is_blocked, _ = bot_security.check_callback_rate_limit(user_id)
                if is_blocked:
                    return
            elif limit_type == 'support' and bot_security.check_support_rate_limit(user_id):
                return
            elif limit_type == 'payments' and bot_security.check_payment_rate_limit(user_id):
                return
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

def validate_message_content(text):
    """Валидация содержимого сообщения"""
    if not text:
        return True, None
    
    # Проверяем на спам
    if bot_security.is_spam_message(text):
        return False, "Сообщение содержит спам"
    
    # Проверяем длину
    if len(text) > 4000:  # Максимальная длина сообщения в Telegram
        return False, "Сообщение слишком длинное"
    
    # Проверяем на потенциально опасные символы
    dangerous_chars = ['<script', 'javascript:', 'data:', 'vbscript:']
    if any(char in text.lower() for char in dangerous_chars):
        return False, "Сообщение содержит потенциально опасный контент"
    
    return True, None
