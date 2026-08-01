import logging
import time
import asyncio
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramBadRequest
from .bot_security import bot_security, telegram_throttler
from config import ADMIN_IDS
from django.core.cache import cache

logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    result = user_id in ADMIN_IDS
    logger.debug(f"middleware is_admin check: user_id={user_id}, ADMIN_IDS={ADMIN_IDS}, result={result}")
    return result

def get_user_display_name(user) -> str:
    """Получить отображаемое имя пользователя"""
    if not user:
        return "Unknown"

    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)

    display_name = " ".join(name_parts) if name_parts else "NoName"
    if user.username:
        display_name += f" (@{user.username})"

    return display_name

def log_user_first_interaction(user_id: int, user, event_type: str = "message"):
    """Логировать первое взаимодействие пользователя с именем"""
    cache_key = f"user_logged_{user_id}"
    if not cache.get(cache_key):
        display_name = get_user_display_name(user)
        logger.info(f"👤 Новый пользователь: {display_name} (ID: {user_id})")
        cache.set(cache_key, True, timeout=86400)  # Логируем раз в день


class RetryWithBackoff:
    """Retry механизм с exponential backoff для Telegram API"""

    def __init__(self, max_retries=3, base_delay=1.0, max_delay=30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def execute_with_retry(self, func, *args, **kwargs):
        """Выполнить функцию с retry"""
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError) as e:
                last_exception = e

                if attempt == self.max_retries:
                    logger.error(f"Все попытки исчерпаны для {func.__name__}: {e}")
                    raise e

                # Пытаемся извлечь user_id для логирования
                user_id = "unknown"
                try:
                    # Ищем user_id в аргументах
                    for arg in args:
                        if hasattr(arg, 'from_user') and hasattr(arg.from_user, 'id'):
                            user_id = arg.from_user.id
                            break
                        elif hasattr(arg, 'user_id'):
                            user_id = arg.user_id
                            break
                except:
                    pass

                # Вычисляем задержку с exponential backoff
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)

                # Добавляем jitter
                delay += (delay * 0.1) * (asyncio.get_event_loop().time() % 1)

                logger.warning(f"Попытка {attempt + 1}/{self.max_retries + 1} для пользователя {user_id} не удалась: {e}. Ждем {delay:.2f} сек")
                await asyncio.sleep(delay)

            except TelegramBadRequest as e:
                # BadRequest не повторяем
                logger.error(f"BadRequest ошибка (не повторяем): {e}")
                raise e
            except Exception as e:
                # Другие ошибки тоже не повторяем
                logger.error(f"Неожиданная ошибка (не повторяем): {e}")
                raise e

        raise last_exception


# Глобальный экземпляр для retry
retry_handler = RetryWithBackoff(max_retries=5, base_delay=0.5, max_delay=10.0)


class ThrottlingMiddleware(BaseMiddleware):
    """Middleware для throttling запросов к Telegram API"""

    def __init__(self):
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """Обрабатываем каждый запрос через throttler"""
        try:
            user_id = None
            if isinstance(event, Message):
                user_id = event.from_user.id if event.from_user else None
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id if event.from_user else None

            # Пропускаем throttling для админов
            if user_id and is_admin(user_id):
                user = None
                if isinstance(event, Message):
                    user = event.from_user
                elif isinstance(event, CallbackQuery):
                    user = event.from_user

                display_name = get_user_display_name(user) if user else "Unknown"
                logger.info(f"👑 Админ {display_name} (ID: {user_id}) - пропуск throttling")
                return await handler(event, data)

            # Логируем только если throttler активен (очередь не пустая)
            if telegram_throttler.semaphore._value < telegram_throttler.max_concurrent:
                logger.info(f"⏳ Throttling активен для пользователя {user_id}, ожидаем слот")

            async with telegram_throttler:
                return await handler(event, data)
        except Exception as e:
            logger.error(f"Ошибка в throttling middleware: {e}")
            # В случае ошибки все равно пытаемся обработать запрос
            return await handler(event, data)


class SecurityMiddleware(BaseMiddleware):
    """Middleware для проверки безопасности"""

    def __init__(self):
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """Проверяем безопасность перед обработкой"""
        try:
            user_id = None

            if isinstance(event, Message):
                user = event.from_user
                user_id = user.id if user else None

                # Логируем первое взаимодействие пользователя
                if user_id:
                    log_user_first_interaction(user_id, user, "message")

                logger.debug(f"📨 Входящее сообщение от пользователя {user_id} в чат {event.chat.id}")

                # Пропускаем все проверки безопасности для админов
                if user_id and is_admin(user_id):
                    # Агрессивно сбрасываем ВСЕ возможные блокировки и лимиты для админа
                    from django.core.cache import cache
                    # Сбрасываем все ключи, связанные с пользователем
                    cache_keys = [
                        f"blocked_user_{user_id}",
                        f"block_notified_{user_id}",
                        f"messages_{user_id}",
                        f"callbacks_{user_id}",
                        f"support_{user_id}",
                        f"payments_{user_id}",
                        f"spam_protection_{user_id}",  # На всякий случай
                        f"user_spam_{user_id}",  # На всякий случай
                    ]
                    for key in cache_keys:
                        cache.delete(key)

                    # Также сбрасываем любые ключи, которые могут содержать user_id
                    # Это более агрессивный подход
                    try:
                        # Получаем все ключи из кеша (если возможно)
                        # В Redis можно использовать scan, но в Django cache это может не работать
                        pass
                    except:
                        pass

                    display_name = get_user_display_name(user)
                    logger.info(f"👑 Админ {display_name} (ID: {user_id}) - пропуск всех проверок безопасности и полный сброс лимитов")
                    logger.info(f"DEBUG: ADMIN_IDS = {ADMIN_IDS}, user_id = {user_id}, is_admin = {is_admin(user_id)}")
                else:
                    # Проверяем бан
                    if user_id:
                        from .models import TelegramUser
                        try:
                            u = TelegramUser.objects.filter(user_id=user_id).first()
                            if u and u.is_banned:
                                logger.info(f"🚫 Забаненный пользователь {user_id} попытался написать")
                                return
                        except Exception:
                            pass
                    # Проверяем лимит сообщений
                    if user_id:
                        is_blocked, should_notify = bot_security.check_message_rate_limit(user_id)
                        if is_blocked:
                            if should_notify:
                                logger.warning(f"🚫 Отправлено уведомление о блокировке пользователю {user_id} за превышение лимита сообщений")
                                # Отправляем уведомление только один раз
                                await safe_send_message(
                                    data.get('bot'),  # Получаем бота из данных
                                    event.chat.id,
                                    "🚫 Вы заблокированы за спам на 1 минуту. Подождите."
                                )
                            else:
                                logger.debug(f"🚫 Заблокированный пользователь {user_id} пытается отправить сообщение (уведомление уже отправлено)")
                            return  # Всегда блокируем заблокированного пользователя

                    # Проверяем содержимое сообщения
                    if event.text and len(event.text) > 4000:
                        return  # Игнорируем слишком длинные сообщения

            elif isinstance(event, CallbackQuery):
                user = event.from_user
                user_id = user.id if user else None

                # Логируем первое взаимодействие пользователя
                if user_id:
                    log_user_first_interaction(user_id, user, "callback")

                logger.info(f"🔘 Callback от пользователя {user_id}")

                # Пропускаем все проверки безопасности для админов
                if user_id and is_admin(user_id):
                    # Сбрасываем возможную блокировку для админа
                    from django.core.cache import cache
                    cache.delete(f"blocked_user_{user_id}")
                    cache.delete(f"block_notified_{user_id}")

                    display_name = get_user_display_name(user)
                    logger.info(f"👑 Админ {display_name} (ID: {user_id}) - пропуск всех проверок безопасности и сброс блокировки")
                else:
                    # Проверяем лимит callback'ов
                    if user_id:
                        is_blocked, should_notify = bot_security.check_callback_rate_limit(user_id)
                        if is_blocked:
                            if should_notify:
                                logger.warning(f"🚫 Отправлено уведомление о блокировке пользователю {user_id} за превышение лимита callback'ов")
                                # Отправляем уведомление только один раз
                                await event.answer("🚫 Вы заблокированы за спам на 1 минуту. Подождите.", show_alert=True)
                            else:
                                logger.debug(f"🚫 Заблокированный пользователь {user_id} пытается нажать кнопку (уведомление уже отправлено)")
                            return  # Всегда блокируем заблокированного пользователя

            # Проверяем блокировку пользователя
            if user_id and bot_security.is_user_blocked(user_id):
                logger.warning(f"Заблокированный пользователь {user_id} пытается взаимодействовать")
                return

            return await handler(event, data)

        except Exception as e:
            logger.error(f"Ошибка в security middleware: {e}")
            return await handler(event, data)


class ErrorHandlingMiddleware(BaseMiddleware):
    """Middleware для обработки ошибок"""

    def __init__(self):
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """Обрабатываем ошибки в обработчиках"""
        try:
            return await handler(event, data)
        except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError) as e:
            logger.warning(f"Network ошибка: {e}. Пытаемся повторить через retry_handler")

            # Для network ошибок пытаемся повторить
            try:
                return await retry_handler.execute_with_retry(handler, event, data)
            except Exception as retry_error:
                logger.error(f"Retry тоже не помог: {retry_error}")
                # Продолжаем к обычной обработке ошибок

        except TelegramBadRequest as e:
            logger.error(f"BadRequest ошибка: {e}")
            # Для BadRequest не отправляем сообщения пользователю

        except Exception as e:
            logger.error(f"Необработанная ошибка в обработчике: {e}", exc_info=True)

            # Пытаемся отправить сообщение об ошибке пользователю
            try:
                if isinstance(event, Message):
                    await retry_handler.execute_with_retry(
                        event.answer, "❌ Произошла ошибка. Попробуйте позже."
                    )
                elif isinstance(event, CallbackQuery):
                    await retry_handler.execute_with_retry(
                        event.answer, "❌ Произошла ошибка. Попробуйте позже.", show_alert=True
                    )
            except Exception:
                pass  # Игнорируем ошибки при отправке сообщения об ошибке

            return


# Вспомогательные функции для безопасной работы с Telegram API

async def safe_send_message(bot, chat_id, text, **kwargs):
    """Безопасная отправка сообщения с retry"""
    try:
        return await retry_handler.execute_with_retry(
            bot.send_message, chat_id, text, **kwargs
        )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение в {chat_id}: {e}")
        return None


async def safe_edit_message(bot, chat_id, message_id, text, **kwargs):
    """Безопасное редактирование сообщения с retry"""
    try:
        return await retry_handler.execute_with_retry(
            bot.edit_message_text, text, chat_id, message_id, **kwargs
        )
    except Exception as e:
        logger.error(f"Не удалось отредактировать сообщение {message_id} в {chat_id}: {e}")
        return None


async def safe_answer_callback(callback_query, text, **kwargs):
    """Безопасный ответ на callback query с retry"""
    try:
        return await retry_handler.execute_with_retry(
            callback_query.answer, text, **kwargs
        )
    except Exception as e:
        logger.error(f"Не удалось ответить на callback query: {e}")
        return None
