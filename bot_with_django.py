import asyncio
import logging
from pathlib import Path
from typing import Dict
from functools import wraps
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ContentType, FSInputFile, InputMediaPhoto
)
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, ADMIN_IDS, PRICES, VIDEO_FILE_ID, VIDEO_URL, VIDEO_POST_URL, OPERATORS, SUPPORT_GROUP_ID, REQUIRED_CHANNEL, REQUIRED_CHANNEL_ID, DISABLE_PHOTOS, REGULAR_VPN_PRICES, FAST_VPN_PRICES, DJANGO_API_URL
from database import init_db, get_db
from bot_management.bot_security import rate_limit_check, validate_message_content, bot_security, telegram_throttler
from bot_management.bot_middlewares import (
    ThrottlingMiddleware, SecurityMiddleware, ErrorHandlingMiddleware,
    safe_send_message, safe_edit_message, safe_answer_callback, retry_handler
)
from notification_scheduler import start_notification_scheduler, stop_notification_scheduler, get_scheduler_status

# Универсальный декоратор для защиты всех сообщений
def protect_message(scope='messages'):
    """Декоратор для защиты сообщений от спама"""
    def decorator(func):
        @wraps(func)
        async def wrapper(message: Message, *args, **kwargs):
            user_id = message.from_user.id

            # Пропускаем все проверки для админов
            if is_admin(user_id):
                logging.info(f"👑 Админ {user_id} пропускает защиту сообщений в {func.__name__}")
                return await func(message, *args, **kwargs)

            # Проверяем, заблокирован ли пользователь
            if bot_security.is_user_blocked(user_id):
                await message.answer("🚫 Вы заблокированы за спам на 1 минуту. Подождите.")
                return

            # Проверяем лимит сообщений (True = заблокирован, False = ОК)
            is_blocked = bot_security.check_message_rate_limit(user_id)
            if is_blocked:
                await message.answer("🚫 Вы заблокированы за спам на 1 минуту. Подождите.")
                return

            # Проверяем содержимое на спам
            if message.text:
                is_valid, error = validate_message_content(message.text)
                if not is_valid:
                    logging.warning(f"Сообщение отклонено: {error}")
                    return

            # Если все ОК, выполняем функцию
            return await func(message, *args, **kwargs)
        return wrapper
    return decorator

# Настройка Django (должна быть перед импортами Django моделей)
import os
import sys
import django
from pathlib import Path

# Добавляем путь к проекту Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')

try:
    django.setup()
    from bot_integration import (
        notify_django_new_user, notify_django_new_payment,
        notify_django_payment_receipt, notify_django_support_message,
        create_yookassa_payment, create_platega_payment, create_cryptobot_payment,
        create_antilopay_payment
    )
    from config import DJANGO_API_URL, CRYPTOBOT_API_TOKEN
    
    # Запускаем проверку с повторными попытками
    import aiohttp
    
    async def check_django_api(max_retries=5, delay=2):
        """Проверка доступности Django API с повторными попытками"""
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{DJANGO_API_URL}/bot_management/api/health/", timeout=2) as response:
                        if response.status == 200:
                            return True
            except Exception as e:
                logging.info(f"Попытка {attempt + 1}/{max_retries}: Django API ещё не доступен ({e})")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
        
        return False
    
    # Запускаем проверку
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    django_available = loop.run_until_complete(check_django_api())
    loop.close()
    
    if django_available:
        DJANGO_INTEGRATION = True
        print(f"✅ Django интеграция активирована ({DJANGO_API_URL})")
    else:
        DJANGO_INTEGRATION = False
        print(f"⚠️ Django API недоступен по адресу {DJANGO_API_URL} - бот работает в автономном режиме")
        
except Exception as e:
    print(f"⚠️ Django интеграция недоступна: {e}")
    DJANGO_INTEGRATION = False
    logging.warning("Django интеграция недоступна")

from bot_management.bot_orm_helpers import (
    get_or_create_support_chat_safe, save_support_message_safe,
    get_payment_safe, update_payment_status_safe, get_user_safe,
    create_payment_safe, get_available_key_safe, update_user_balance_safe
)
from bot_management.services import KEY_DELIVERY_MESSAGE_1, KEY_DELIVERY_MESSAGE_2

# Кэш для file_id изображений (чтобы не загружать файлы каждый раз)
_image_file_id_cache = {}

async def get_cached_file_id(image_name: str) -> str | None:
    """Получает file_id из кэша или загружает и кэширует его"""
    if image_name in _image_file_id_cache:
        return _image_file_id_cache[image_name]
    
    try:
        image_path = Path(f"images/{image_name}")
        if not image_path.exists():
            return None
        
        # Загружаем файл один раз и получаем file_id
        photo_file = FSInputFile(image_path)
        # Отправляем себе (админу) чтобы получить file_id
        chat_id = ADMIN_IDS[0] if ADMIN_IDS else 1
        msg = await bot.send_photo(chat_id=chat_id, photo=photo_file)
        file_id = msg.photo[-1].file_id
        # Удаляем тестовое сообщение
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        
        _image_file_id_cache[image_name] = file_id
        logging.info(f"🖼 Кэширован file_id для {image_name}")
        return file_id
    except Exception as e:
        logging.warning(f"Не удалось кэшировать file_id для {image_name}: {e}")
        return None

async def cache_all_images():
    """Загружает все изображения в кэш file_id при старте бота"""
    images_dir = Path("images")
    if not images_dir.exists() or DISABLE_PHOTOS:
        return
    
    image_files = list(images_dir.glob("*.png")) + list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.jpeg"))
    logging.info(f"🖼 Кэширование {len(image_files)} изображений...")
    
    for image_path in image_files:
        try:
            photo_file = FSInputFile(image_path)
            chat_id = ADMIN_IDS[0] if ADMIN_IDS else 1
            msg = await bot.send_photo(chat_id=chat_id, photo=photo_file)
            file_id = msg.photo[-1].file_id
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            _image_file_id_cache[image_path.name] = file_id
            logging.info(f"✅ Кэширован {image_path.name}")
        except Exception as e:
            logging.warning(f"Не удалось кэшировать {image_path.name}: {e}")
    
    logging.info(f"🖼 Кэшировано {len(_image_file_id_cache)} изображений")


# Функция для отправки приветственного сообщения с фотографией
async def send_welcome_with_photo(message: Message, text: str, reply_markup=None, reply_keyboard=None):
    """Отправляет приветственное сообщение с фотографией из папки images"""
    return await send_or_edit_message_with_photo(message, text, reply_markup, edit_message=False, reply_keyboard=reply_keyboard)

# Функция для сравнения клавиатур
def keyboards_are_equal(kb1, kb2):
    """Сравнивает две клавиатуры на равенство"""
    if kb1 is None and kb2 is None:
        return True
    if kb1 is None or kb2 is None:
        return False
    
    try:
        # Сравниваем inline_keyboard если это InlineKeyboardMarkup
        if hasattr(kb1, 'inline_keyboard') and hasattr(kb2, 'inline_keyboard'):
            kb1_buttons = kb1.inline_keyboard
            kb2_buttons = kb2.inline_keyboard
            
            if len(kb1_buttons) != len(kb2_buttons):
                return False
                
            for row1, row2 in zip(kb1_buttons, kb2_buttons):
                if len(row1) != len(row2):
                    return False
                for i in range(len(row1)):
                    btn1 = row1[i]
                    btn2 = row2[i]
                    if btn1.text != btn2.text or btn1.callback_data != btn2.callback_data:
                        return False
            return True
        else:
            # Для других типов клавиатур используем строковое сравнение
            return str(kb1) == str(kb2)
    except Exception as e:
        logging.warning(f"Ошибка сравнения клавиатур: {e}")
        return False

# Функция для обрезки длинных сообщений
def truncate_message(text: str, max_length: int = 4090) -> str:
    """Обрезает сообщение до максимальной длины с добавлением многоточия"""
    if len(text) <= max_length:
        return text
    
    # Ищем последний полный тег или слово
    truncated = text[:max_length]
    
    # Пытаемся найти последний закрывающий тег
    last_tag = truncated.rfind('</')
    if last_tag > max_length - 50:  # Если тег не слишком далеко от конца
        truncated = truncated[:last_tag] + '</b></i>'
    else:
        truncated = truncated.rstrip() + "..."
    
    return truncated

# Функция для проверки валидности сообщения
def is_valid_message(text: str) -> bool:
    """Проверяет, является ли сообщение валидным для отправки"""
    if not text or not text.strip():
        return False
    
    # Проверяем, что сообщение не состоит только из HTML тегов
    import re
    clean_text = re.sub(r'<[^>]+>', '', text).strip()
    if not clean_text:
        return False
    
    # Проверяем, что сообщение не состоит только из эмодзи и пробелов
    emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U0001F018-\U0001F0F5\U0001F200-\U0001F2FF\s]+')
    if emoji_pattern.fullmatch(clean_text):
        return False
    
    return True


# Универсальная функция для отправки/редактирования сообщений с изображениями
async def send_or_edit_message_with_photo(message_or_callback, text: str, reply_markup=None, edit_message=True, image_name="hellonightvpn.png", reply_keyboard=None):
    """
    Универсальная функция для отправки или редактирования сообщений с изображениями

    Args:
        message_or_callback: Message или CallbackQuery объект
        text: Текст сообщения
        reply_markup: Inline клавиатура
        edit_message: True - редактировать существующее сообщение, False - отправить новое
        image_name: Имя файла изображения в папке images/
        reply_keyboard: Reply клавиатура (обычная клавиатура)
    """
    try:
        # Проверяем валидность сообщения
        if not is_valid_message(text):
            logging.warning("Попытка отправить невалидное сообщение")
            return False

        # Проверяем, отключены ли фото
        if DISABLE_PHOTOS:
            # Отправляем только текст без фото
            is_callback = hasattr(message_or_callback, 'message')
            
            if edit_message:
                try:
                    if is_callback:
                        await message_or_callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
                    else:
                        await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
                except Exception as e:
                    if "message is not modified" in str(e):
                        logging.info("Сообщение не изменилось, пропускаем редактирование")
                    else:
                        # Если нельзя редактировать, отправляем новое
                        if is_callback:
                            await message_or_callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
                        else:
                            await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
            else:
                # Отправляем новое сообщение
                if is_callback:
                    await message_or_callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
                else:
                    await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
            
            return True

        # Ищем изображение (если фото не отключены)
        image_path = Path(f"images/{image_name}")
        if image_path.exists():
            logging.info(f"Отправляем/редактируем с фотографией: {image_path}")
            
            # Для подписей к фото Telegram ограничивает до 1024 символов
            # Обрезаем текст если он слишком длинный для подписи
            caption_text = truncate_message(text, max_length=1024)
            # Если текст был обрезан, сохраняем полный текст для отдельного сообщения
            full_text = text if len(text) <= 1024 else None
            
            # Используем оригинальное изображение без обработки
            processed_image_path = image_path
            
            # Получаем кэшированный file_id (мгновенно) или загружаем файл
            cached_file_id = await get_cached_file_id(image_name)
            
            # Определяем тип объекта
            is_callback = hasattr(message_or_callback, 'message')
            target_message = message_or_callback.message if is_callback else message_or_callback
            
            if edit_message and target_message.photo:
                # Проверяем, изменилась ли подпись или клавиатура
                current_caption = target_message.caption or ""
                current_reply_markup = target_message.reply_markup
                text_changed = current_caption != caption_text
                keyboard_changed = not keyboards_are_equal(current_reply_markup, reply_markup)
                
                if text_changed or keyboard_changed:
                    # Если сообщение уже содержит фото, редактируем его
                    try:
                        from aiogram.types import InputMediaPhoto
                        if cached_file_id:
                            media = InputMediaPhoto(media=cached_file_id, caption=caption_text, parse_mode="HTML")
                        else:
                            photo_file = FSInputFile(processed_image_path)
                            media = InputMediaPhoto(media=photo_file, caption=caption_text, parse_mode="HTML")
                        await target_message.edit_media(
                            media=media,
                            reply_markup=reply_markup
                        )
                        
                        # Если текст был обрезан, отправляем остаток отдельным сообщением
                        if full_text is None and len(text) > 1024:
                            # Отправляем остаток текста отдельным сообщением
                            remaining_text = text[1024:]
                            if is_callback:
                                await message_or_callback.message.answer(remaining_text, parse_mode="HTML")
                            else:
                                await message_or_callback.answer(remaining_text, parse_mode="HTML")
                    except Exception as e:
                        if "message is not modified" in str(e):
                            logging.info("Содержимое фото не изменилось, пропускаем редактирование")
                        elif "caption is too long" in str(e).lower():
                            # Если подпись все еще слишком длинная, отправляем фото без подписи
                            logging.warning(f"Подпись слишком длинная, отправляем фото без подписи: {e}")
                            try:
                                from aiogram.types import InputMediaPhoto
                                if cached_file_id:
                                    media = InputMediaPhoto(media=cached_file_id)
                                else:
                                    photo_file = FSInputFile(processed_image_path)
                                    media = InputMediaPhoto(media=photo_file)
                                await target_message.edit_media(
                                    media=media,
                                    reply_markup=reply_markup
                                )
                                # Отправляем текст отдельным сообщением
                                if is_callback:
                                    await message_or_callback.message.answer(text, parse_mode="HTML")
                                else:
                                    await message_or_callback.answer(text, parse_mode="HTML")
                            except Exception as e2:
                                logging.error(f"Ошибка отправки фото без подписи: {e2}")
                        else:
                            logging.error(f"Ошибка редактирования фото: {e}")
                else:
                    # Содержимое не изменилось, не редактируем
                    logging.info("Содержимое фото и клавиатура не изменились, пропускаем редактирование")
            elif edit_message:
                # Если сообщение без фото, но нужно редактировать - отправляем новое с фото
                try:
                    if cached_file_id:
                        if is_callback:
                            await message_or_callback.message.answer_photo(
                                photo=cached_file_id,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                        else:
                            await message_or_callback.answer_photo(
                                photo=cached_file_id,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                    else:
                        photo_file = FSInputFile(processed_image_path)
                        if is_callback:
                            await message_or_callback.message.answer_photo(
                                photo=photo_file,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                        else:
                            await message_or_callback.answer_photo(
                                photo=photo_file,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                    
                    # Если текст был обрезан, отправляем остаток отдельным сообщением
                    if full_text is None and len(text) > 1024:
                        remaining_text = text[1024:]
                        if is_callback:
                            await message_or_callback.message.answer(remaining_text)
                        else:
                            await message_or_callback.answer(remaining_text)
                except Exception as e:
                    if "caption is too long" in str(e).lower():
                        # Если подпись все еще слишком длинная, отправляем фото без подписи
                        logging.warning(f"Подпись слишком длинная, отправляем фото без подписи: {e}")
                        if cached_file_id:
                            if is_callback:
                                await message_or_callback.message.answer_photo(
                                    photo=cached_file_id,
                                    reply_markup=reply_markup
                                )
                            else:
                                await message_or_callback.answer_photo(
                                    photo=cached_file_id,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                        else:
                            photo_file = FSInputFile(processed_image_path)
                            if is_callback:
                                await message_or_callback.message.answer_photo(
                                    photo=photo_file,
                                    reply_markup=reply_markup
                                )
                            else:
                                await message_or_callback.answer_photo(
                                    photo=photo_file,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                        if is_callback:
                            await message_or_callback.message.answer(text, parse_mode="HTML")
                        else:
                            await message_or_callback.answer(text, parse_mode="HTML")
                    else:
                        raise
            else:
                # Отправляем новое сообщение с фото
                try:
                    if cached_file_id:
                        if is_callback:
                            await message_or_callback.message.answer_photo(
                                photo=cached_file_id,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                        else:
                            await message_or_callback.answer_photo(
                                photo=cached_file_id,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                    else:
                        photo_file = FSInputFile(processed_image_path)
                        if is_callback:
                            await message_or_callback.message.answer_photo(
                                photo=photo_file,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                        else:
                            await message_or_callback.answer_photo(
                                photo=photo_file,
                                caption=caption_text,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                                reply_keyboard=reply_keyboard
                            )
                    
                    # Если текст был обрезан, отправляем остаток отдельным сообщением
                    if full_text is None and len(text) > 1024:
                        remaining_text = text[1024:]
                        if is_callback:
                            await message_or_callback.message.answer(remaining_text, parse_mode="HTML")
                        else:
                            await message_or_callback.answer(remaining_text, parse_mode="HTML")
                except Exception as e:
                    if "caption is too long" in str(e).lower():
                        # Если подпись все еще слишком длинная, отправляем фото без подписи
                        logging.warning(f"Подпись слишком длинная, отправляем фото без подписи: {e}")
                        if cached_file_id:
                            if is_callback:
                                await message_or_callback.message.answer_photo(
                                    photo=cached_file_id,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                            else:
                                await message_or_callback.answer_photo(
                                    photo=cached_file_id,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                        else:
                            photo_file = FSInputFile(processed_image_path)
                            if is_callback:
                                await message_or_callback.message.answer_photo(
                                    photo=photo_file,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                            else:
                                await message_or_callback.answer_photo(
                                    photo=photo_file,
                                    reply_markup=reply_markup,
                                    reply_keyboard=reply_keyboard
                                )
                        if is_callback:
                            await message_or_callback.message.answer(text, parse_mode="HTML")
                        else:
                            await message_or_callback.answer(text, parse_mode="HTML")
                    else:
                        raise
            
            # Удаляем временный файл если он был создан
            if processed_image_path != image_path and processed_image_path.exists():
                processed_image_path.unlink()
                logging.info("Временный файл удален")
            
            return True
        else:
            logging.warning(f"Файл {image_path} не найден")
    except Exception as e:
        logging.error(f"Ошибка отправки/редактирования фотографии: {e}")
    
    # Если не удалось отправить фото, используем обычный текст
    try:
        is_callback = hasattr(message_or_callback, 'message')
        
        if edit_message:
            # Проверяем, можно ли редактировать сообщение
            if is_callback and hasattr(message_or_callback.message, 'text') and message_or_callback.message.text:
                # Проверяем, изменилось ли содержимое или клавиатура
                current_text = message_or_callback.message.text
                current_reply_markup = message_or_callback.message.reply_markup
                text_changed = current_text != text
                keyboard_changed = not keyboards_are_equal(current_reply_markup, reply_markup)
                
                if text_changed or keyboard_changed:
                    # Сообщение содержит текст, можно редактировать
                    try:
                        await message_or_callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
                    except Exception as e:
                        if "message is not modified" in str(e):
                            logging.info("Содержимое текстового сообщения не изменилось, пропускаем редактирование")
                        else:
                            logging.error(f"Ошибка редактирования текста: {e}")
                else:
                    # Содержимое не изменилось, не редактируем
                    logging.info("Содержимое сообщения и клавиатура не изменились, пропускаем редактирование")
            elif is_callback and hasattr(message_or_callback.message, 'photo') and message_or_callback.message.photo:
                # Проверяем, изменилась ли подпись или клавиатура
                current_caption = message_or_callback.message.caption or ""
                current_reply_markup = message_or_callback.message.reply_markup
                text_changed = current_caption != text
                keyboard_changed = not keyboards_are_equal(current_reply_markup, reply_markup)
                
                if text_changed or keyboard_changed:
                    # Сообщение содержит фото, редактируем подпись
                    try:
                        await message_or_callback.message.edit_caption(
                            caption=text,
                            parse_mode="HTML",
                            reply_markup=reply_markup
                        )
                    except Exception as e:
                        if "message is not modified" in str(e):
                            logging.info("Подпись фото не изменилась, пропускаем редактирование")
                        else:
                            logging.error(f"Ошибка редактирования подписи: {e}")
                else:
                    # Содержимое не изменилось, не редактируем
                    logging.info("Подпись сообщения и клавиатура не изменились, пропускаем редактирование")
            else:
                # Не можем редактировать, отправляем новое
                if is_callback:
                    await message_or_callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
                else:
                    await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
        else:
            # Отправляем новое сообщение
            if is_callback:
                await message_or_callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
            else:
                await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup, reply_keyboard=reply_keyboard)
    except Exception as e:
        logging.error(f"Ошибка отправки текстового сообщения: {e}")
        # Последняя попытка - просто ответить
        try:
            if is_callback:
                await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await message_or_callback.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as final_e:
            if "message is not modified" in str(final_e):
                logging.info("Содержимое сообщения не изменилось в последней попытке, пропускаем")
            else:
                logging.error(f"Критическая ошибка отправки сообщения: {final_e}")
                # Отправляем простое сообщение без форматирования
                try:
                    simple_text = text.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '')
                    if is_callback:
                        await message_or_callback.answer(simple_text)
                    else:
                        await message_or_callback.answer(simple_text)
                except:
                    pass  # Если даже это не работает, просто игнорируем
    
    return False

# Функция для отправки приветственного сообщения с фотографией через callback
async def send_welcome_with_photo_callback(callback: CallbackQuery, text: str, reply_markup=None):
    """Отправляет приветственное сообщение с фотографией через callback"""
    return await send_or_edit_message_with_photo(callback, text, reply_markup, edit_message=True)

# Django уже настроен выше

# Логирование
logging.basicConfig(
    level=logging.DEBUG,  # Изменено с INFO на DEBUG для показа user_id в логах
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Инициализация
init_db()

from config import TELEGRAM_PROXY

# Создаём сессию с прокси если настроен
if TELEGRAM_PROXY:
    from aiogram.client.session.aiohttp import AioHttpSession
    proxy_session = AioHttpSession(proxy=TELEGRAM_PROXY)
    bot = Bot(token=BOT_TOKEN, session=proxy_session)
else:
    bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())

# Добавляем middleware для защиты от DDoS ПЕРЕД включением router
# dp.message.middleware(ThrottlingMiddleware())  # Временно отключено
# dp.callback_query.middleware(ThrottlingMiddleware())  # Временно отключено
dp.message.middleware(SecurityMiddleware())
dp.callback_query.middleware(SecurityMiddleware())
dp.message.middleware(ErrorHandlingMiddleware())
dp.callback_query.middleware(ErrorHandlingMiddleware())

router = Router()
dp.include_router(router)

# Подключаем обработчики выплат
from bot_management.payout_handlers import router as payout_router
dp.include_router(payout_router)

# HTTP сервер для получения уведомлений от Django
from aiohttp import web
import aiohttp
from aiohttp import ClientSession, TCPConnector

# Глобальная сессия aiohttp с connection pooling для оптимизации
_http_session = None

def get_http_session():
    """Получить или создать глобальную HTTP сессию с connection pooling"""
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = TCPConnector(
            limit=100,  # Максимум соединений
            limit_per_host=30,  # Максимум соединений на хост
            ttl_dns_cache=300,  # Кэш DNS на 5 минут
            force_close=False,  # Переиспользование соединений
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        _http_session = ClientSession(connector=connector, timeout=timeout)
    return _http_session

async def handle_withdrawal_notification(request):
    """HTTP endpoint для получения уведомлений о заявках на вывод от Django"""
    try:
        data = await request.json()
        notification_type = data.get('notification_type')
        withdrawal_data = data.get('withdrawal_data')
        
        if not notification_type or not withdrawal_data:
            return web.json_response({
                'success': False,
                'message': 'Не все обязательные поля заполнены'
            }, status=400)
        
        # Отправляем уведомление в Telegram
        await send_withdrawal_notification(notification_type, withdrawal_data)
        
        return web.json_response({'success': True, 'message': 'Уведомление отправлено'})
        
    except Exception as e:
        logging.error(f"Ошибка обработки уведомления о заявке на вывод: {e}")
        return web.json_response({
            'success': False,
            'message': 'Внутренняя ошибка сервера'
        }, status=500)

async def handle_platega_callback(request):
    """
    HTTP endpoint для получения callback от Platega напрямую в бот
    Согласно документации: https://docs.platega.io/callback-об-изменении-статуса-транзакции-22645075e0
    
    Используется когда нет домена - через ngrok или другой туннель
    Также используется как универсальный webhook endpoint для всех платежных систем
    """
    try:
        import json
        from config import PLATEGA_MERCHANT_ID, PLATEGA_SECRET
        
        logging.info("DEBUG: Получен POST запрос на webhook")
        
        # Проверка авторизации через заголовки (для Platega)
        merchant_id = request.headers.get('X-MerchantId')
        secret = request.headers.get('X-Secret')
        
        # Если заголовки отсутствуют, это может быть универсальный webhook без авторизации
        if merchant_id and secret:
            # Проверяем учетные данные Platega
            if merchant_id != PLATEGA_MERCHANT_ID or secret != PLATEGA_SECRET:
                logging.error(f"DEBUG: Неверные учетные данные")
                return web.json_response({'status': 'error', 'message': 'Unauthorized'}, status=401)
            logging.info("DEBUG: Авторизация Platega успешна")
        else:
            logging.info("DEBUG: Запрос без авторизации Platega (универсальный webhook)")
        
        # Получаем данные callback из JSON тела
        try:
            callback_data = await request.json()
        except Exception as e:
            logging.error(f"DEBUG: Ошибка парсинга JSON: {e}")
            return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        
        logging.info(f"DEBUG: Получен webhook: {callback_data}")
        
        # Валидация обязательных полей (Platega формат)
        required_fields = ['id', 'amount', 'currency', 'status']
        missing_fields = [field for field in required_fields if field not in callback_data]
        if missing_fields:
            logging.error(f"DEBUG: Отсутствуют обязательные поля: {missing_fields}")
            return web.json_response({'status': 'error', 'message': f'Missing required fields: {missing_fields}'}, status=400)
        
        # Обрабатываем callback
        transaction_id = callback_data.get('id')
        status = callback_data.get('status', '').upper()
        
        logging.info(f"DEBUG: Transaction ID: {transaction_id}, Status: {status}")
        
        # Обрабатываем через Django если доступен
        if DJANGO_INTEGRATION:
            try:
                from asgiref.sync import sync_to_async
                from bot_management.platega_service import PlategaService
                
                # Вызываем синхронную функцию через sync_to_async
                # skip_notification=True, так как бот сам отправит уведомление после успешной обработки
                result = await sync_to_async(PlategaService.process_webhook)(
                    callback_data, 
                    merchant_id=merchant_id, 
                    secret=secret,
                    skip_notification=True
                )
                
                if result:
                    logging.info("DEBUG: Callback успешно обработан через Django")
                    return web.json_response({'status': 'ok'}, status=200)
                else:
                    logging.warning("DEBUG: Callback обработан с ошибкой")
                    return web.json_response({'status': 'ok', 'message': 'Received but processing failed'}, status=200)
            except Exception as e:
                logging.error(f"DEBUG: Ошибка обработки через Django: {e}")
                import traceback
                logging.error(traceback.format_exc())
                # Продолжаем обработку через локальную БД
        else:
            logging.warning("DEBUG: Django интеграция недоступна, обрабатываем через локальную БД")
        
        # Обработка через локальную SQLite БД (если Django недоступен)
        try:
            with get_db() as conn:
                # Находим платеж по transaction_id
                cursor = conn.execute("""
                    SELECT payment_id, user_id, status, subscription_type, amount, issued_key
                    FROM payments
                    WHERE platega_transaction_id = ?
                """, (transaction_id,))
                
                payment = cursor.fetchone()
                
                if not payment:
                    logging.warning(f"DEBUG: Платеж с transaction_id {transaction_id} не найден в БД")
                    return web.json_response({'status': 'ok', 'message': 'Payment not found'}, status=200)
                
                payment_id, user_id, payment_status, subscription_type, amount, issued_key = payment
                
                logging.info(f"DEBUG: Найден платеж: payment_id={payment_id}, user_id={user_id}, status={payment_status}, issued_key={issued_key}")
                
                # ===== ПРОВЕРКА: КЛЮЧ УЖЕ ВЫДАН =====
                if issued_key:
                    logging.info(f"DEBUG: Ключ для платежа {payment_id} уже выдан, пропускаем обработку")
                    return web.json_response({'status': 'ok', 'message': 'Key already issued'}, status=200)
                
                # Обработка статусов
                if status == 'CONFIRMED':
                    if payment_status == 'succeeded':
                        logging.info(f"DEBUG: Платеж {payment_id} уже обработан")
                        return web.json_response({'status': 'ok'}, status=200)
                    
                    # Обновляем статус (но НЕ подтверждаем автоматически)
                    logging.info(f"DEBUG: Платеж {payment_id} успешен в Platega (CONFIRMED), обновляем статус")
                    conn.execute("""
                        UPDATE payments
                        SET status = 'pending'
                        WHERE payment_id = ?
                    """, (payment_id,))
                    
                    # Уведомляем пользователя, что нужно проверить платеж
                    try:
                        await bot.send_message(
                            user_id,
                            f"""✅ <b>Платеж получен!</b>

💳 <b>Сумма:</b> {amount} ₽
📦 <b>Подписка:</b> {subscription_type}

🔄 <b>Нажмите кнопку "Проверить платеж" для подтверждения и получения ключа</b>

<i>Обычно платеж подтверждается в течение нескольких минут</i>""",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"DEBUG: Ошибка отправки уведомления пользователю {user_id}: {e}")
                    
                    logging.info(f"DEBUG: Платеж {payment_id} обработан")
                    return web.json_response({'status': 'ok'}, status=200)
                    
                elif status == 'CANCELED':
                    # Отменяем платеж
                    conn.execute("""
                        UPDATE payments
                        SET status = 'canceled'
                        WHERE payment_id = ?
                    """, (payment_id,))
                    
                    logging.info(f"DEBUG: Платеж {payment_id} отменен")
                    return web.json_response({'status': 'ok'}, status=200)
                else:
                    logging.warning(f"DEBUG: Неизвестный статус: {status}")
                    return web.json_response({'status': 'ok'}, status=200)
                    
        except Exception as e:
            logging.error(f"DEBUG: Ошибка обработки через локальную БД: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return web.json_response({'status': 'ok'}, status=200)  # Все равно возвращаем 200
        
    except Exception as e:
        logging.error(f"DEBUG: Ошибка обработки callback: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return web.json_response({'status': 'error', 'message': str(e)}, status=200)  # Все равно 200 для Platega

async def init_http_server():
    """Инициализация HTTP сервера для бота"""
    app = web.Application()
    app.router.add_post('/api/withdrawal/notification/', handle_withdrawal_notification)
    # Endpoint для Platega callback напрямую в бот
    app.router.add_post('/api/platega/callback/', handle_platega_callback)
    # Универсальный webhook endpoint для всех платежных систем
    app.router.add_post('/webhook', handle_platega_callback)
    
    runner = web.AppRunner(app)
    await runner.setup()
    # Слушаем на всех интерфейсах (0.0.0.0) для доступа через ngrok
    site = web.TCPSite(runner, '0.0.0.0', 8023)
    await site.start()
    logging.info("HTTP сервер бота запущен на порту 8023 (0.0.0.0)")
    logging.info("Для публичного доступа используйте ngrok: ngrok http 8023")
    logging.info("URL для Platega callback: https://ваш-ngrok-url.ngrok.io/api/platega/callback/")
    logging.info("URL для универсального webhook: http://188.215.229.165/webhook")

# Проверяем настройки
print(f"SUPPORT_GROUP_ID: {SUPPORT_GROUP_ID}")
print(f"ADMIN_IDS: {ADMIN_IDS}")

# Временно устанавливаем SUPPORT_GROUP_ID для тестирования
if not SUPPORT_GROUP_ID:
    SUPPORT_GROUP_ID = "-1001234567890"  # Замените на реальный ID группы
    print(f"Установлен временный SUPPORT_GROUP_ID: {SUPPORT_GROUP_ID}")

# Функция для отправки уведомлений о заявках на вывод
async def send_withdrawal_notification(notification_type: str, withdrawal_data: dict):
    """Отправляет уведомления о заявках на вывод в группу и пользователю"""
    try:
        logging.info(f"Отправляем уведомление типа: {notification_type}")
        logging.info(f"Данные заявки: {withdrawal_data}")
        
        user_id = withdrawal_data.get('user_id')
        amount = withdrawal_data.get('amount')
        payment_method = withdrawal_data.get('payment_method')
        payment_details = withdrawal_data.get('payment_details')
        username = withdrawal_data.get('username', 'N/A')
        first_name = withdrawal_data.get('first_name', '')
        
        # Определяем название способа выплаты
        method_names = {
            "bank_card": "Банковская карта",
            "yoomoney": "ЮMoney",
            "sberbank": "Сбербанк",
            "tinkoff": "Тинькофф"
        }
        method_name = method_names.get(payment_method, payment_method)
        
        if notification_type == 'new_request':
            # Уведомление в группу о новой заявке
            group_message = f"""
🔔 <b>Новая заявка на вывод средств</b>

👤 <b>Пользователь:</b> @{username} ({first_name})
🆔 <b>ID:</b> {user_id}
💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details[:20]}...

⏰ <b>Требует обработки</b>
"""
            
            # Уведомление пользователю
            user_message = f"""
✅ <b>Заявка на вывод создана</b>

💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details}

⏳ <b>Статус:</b> В обработке
📋 <b>Ваша заявка передана администратору</b>

⏰ <b>Ожидайте уведомления о статусе</b>
"""
            
        elif notification_type == 'approved':
            # Уведомление в группу об одобрении
            group_message = f"""
✅ <b>Заявка на вывод одобрена</b>

👤 <b>Пользователь:</b> @{username} ({first_name})
🆔 <b>ID:</b> {user_id}
💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}

🎯 <b>Готово к выплате</b>
"""
            
            # Уведомление пользователю
            user_message = f"""
🎉 <b>Заявка на вывод одобрена!</b>

💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details}

✅ <b>Статус:</b> Одобрено
⏰ <b>Ожидайте выплаты</b>

💡 <b>Средства будут переведены в ближайшее время</b>
"""
            
        elif notification_type == 'completed':
            # Уведомление в группу о завершении выплаты
            group_message = f"""
💰 <b>Выплата завершена</b>

👤 <b>Пользователь:</b> @{username} ({first_name})
🆔 <b>ID:</b> {user_id}
💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}

✅ <b>Выплата успешно завершена</b>
"""
            
            # Уведомление пользователю
            user_message = f"""
🎉 <b>Выплата завершена!</b>

💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details}

✅ <b>Статус:</b> Выплачено
💳 <b>Средства переведены на указанные реквизиты</b>

🙏 <b>Спасибо за использование нашего сервиса!</b>
"""
            
        elif notification_type == 'rejected':
            # Уведомление в группу об отклонении
            group_message = f"""
❌ <b>Заявка на вывод отклонена</b>

👤 <b>Пользователь:</b> @{username} ({first_name})
🆔 <b>ID:</b> {user_id}
💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}

🚫 <b>Заявка отклонена</b>
"""
            
            # Уведомление пользователю
            user_message = f"""
❌ <b>Заявка на вывод отклонена</b>

💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}

🚫 <b>Статус:</b> Отклонено
💡 <b>Средства возвращены на реферальный баланс</b>

📞 <b>По вопросам обращайтесь в поддержку</b>
"""
        
        # Отправляем уведомление в группу (если указан ID группы)
        if SUPPORT_GROUP_ID:
            try:
                logging.info(f"Отправляем уведомление в группу {SUPPORT_GROUP_ID}")
                await bot.send_message(
                    chat_id=SUPPORT_GROUP_ID,
                    text=group_message,
                    parse_mode="HTML"
                )
                logging.info(f"Уведомление в группу отправлено успешно")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления в группу: {e}")
        else:
            logging.warning("SUPPORT_GROUP_ID не установлен, уведомление в группу не отправлено")
        
        # Отправляем уведомление каждому админу
        for admin_id in ADMIN_IDS:
            try:
                logging.info(f"Отправляем уведомление админу {admin_id}")
                await bot.send_message(
                    chat_id=admin_id,
                    text=group_message,
                    parse_mode="HTML"
                )
                logging.info(f"Уведомление админу {admin_id} отправлено успешно")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
        
        # Отправляем уведомление пользователю
        try:
            await bot.send_message(
                chat_id=user_id,
                text=user_message,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления о заявке на вывод: {e}")

# FSM состояния
class SupportState(StatesGroup):
    in_chat = State()
    waiting_for_message = State()  # Ожидание сообщения от пользователя
    reply_allowed = State()  # Разрешено отправить одно сообщение в поддержку


class AdminState(StatesGroup):
    waiting_user_id = State()

class ReferralState(StatesGroup):
    waiting_code = State()

class UploadKeysState(StatesGroup):
    waiting_keys = State()

class ReferrersSearchState(StatesGroup):
    waiting_search = State()
    waiting_type = State()
    waiting_activations = State()

class BroadcastState(StatesGroup):
    waiting_message = State()
    waiting_confirmation = State()

class AddCardState(StatesGroup):
    waiting_last4 = State()
    waiting_bank = State()
    waiting_limit = State()

class PromoState(StatesGroup):
    waiting_code = State()

class AdminPromoState(StatesGroup):
    waiting_code = State()
    waiting_discount = State()
    waiting_max_per_user = State()
    waiting_max_uses = State()

# BalanceState удален - теперь используем простые кнопки

# Хранилище pending платежей (до применения промокода или оплаты)
pending_payments: Dict[int, Dict] = {}
pending_promo_users: set = set()

# Утилиты
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def schedule_pending_payment_reminder(payment_id: int, user_id: int, subscription_type: str, amount: int):
    """Отложенная отправка напоминания о незавершенном платеже через 30 минут"""
    try:
        # Ждем 30 минут (1800 секунд)
        await asyncio.sleep(1800)

        # Проверяем статус платежа через Django API
        if DJANGO_INTEGRATION:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/status/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'pending':
                            # Платеж все еще не оплачен, отправляем напоминание
                            await send_pending_payment_reminder(user_id, subscription_type, amount)
                    else:
                        logging.warning(f"Не удалось проверить статус платежа {payment_id}")
        else:
            # Проверяем через SQLite
            with get_db() as conn:
                payment = conn.execute("SELECT status FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
                if payment and payment['status'] == 'pending':
                    # Платеж все еще не оплачен, отправляем напоминание
                    await send_pending_payment_reminder(user_id, subscription_type, amount)

    except Exception as e:
        logging.error(f"Ошибка в отложенном напоминании для платежа {payment_id}: {e}")

async def schedule_subscription_expiry_reminders(payment_id: int, user_id: int, subscription_type: str, expires_at):
    """Отложенная отправка напоминаний об истечении подписки"""
    try:
        from datetime import datetime, timezone
        import time

        # Преобразуем expires_at в timestamp
        if isinstance(expires_at, str):
            # Если expires_at - строка, парсим её
            from dateutil import parser
            expires_at = parser.parse(expires_at)
        elif expires_at.tzinfo is None:
            # Если naive datetime, считаем что UTC
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        expires_timestamp = expires_at.timestamp()
        now_timestamp = now.timestamp()

        # Если подписка уже истекла, не запускаем напоминания
        if expires_timestamp <= now_timestamp:
            return

        # Время до истечения в секундах
        time_until_expiry = expires_timestamp - now_timestamp

        # Напоминание за 2 дня (172800 секунд)
        reminder_2_days = time_until_expiry - 172800
        if reminder_2_days > 0:
            # Запускаем напоминание за 2 дня
            asyncio.create_task(send_subscription_reminder_later(user_id, subscription_type, reminder_2_days, '2_days_before'))

        # Напоминание при истечении
        asyncio.create_task(send_subscription_reminder_later(user_id, subscription_type, time_until_expiry, 'expired'))

    except Exception as e:
        logging.error(f"Ошибка планирования напоминаний об истечении подписки для платежа {payment_id}: {e}")

async def switch_user_to_expired_squad(user_id: int):
    """
    Переключает пользователя на сквад с ограниченным доступом (ремнаутрон) при истечении подписки.
    Вырубает все текущие сквады и добавляет только сквад "22a6415e-db7b-486c-8c8a-ccecf42d8459"
    """
    try:
        from bot_management.remnawave_api import get_remnawave_bypass_client
        from config import REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID
        
        remnawave_client = get_remnawave_bypass_client()
        if not remnawave_client:
            logging.error(f'Remnawave Bypass клиент не инициализирован для пользователя {user_id}')
            return
        
        expired_squad_uuid = REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID
        if not expired_squad_uuid:
            logging.error(f'REMNAWAVE_EXPIRED_SUBSCRIPTION_SQUAD_UUID не настроен')
            return
        
        # Обновляем сквады пользователя - устанавливаем только сквад с ограниченным доступом
        await remnawave_client.update_user_squads(user_id, [expired_squad_uuid])
        
        logging.info(f'Пользователь {user_id} переключен на сквад истекшей подписки {expired_squad_uuid}')
        
    except Exception as e:
        logging.error(f'Ошибка переключения пользователя {user_id} на сквад истекшей подписки: {e}')
        raise

async def send_subscription_reminder_later(user_id: int, subscription_type: str, delay_seconds: float, reminder_type: str):
    """Отправляет напоминание о подписке через указанное время"""
    try:
        # Ждем нужное время
        await asyncio.sleep(delay_seconds)

        # Определяем текст напоминания
        sub_names = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }
        sub_name = sub_names.get(subscription_type, 'Подписка')

        if reminder_type == '2_days_before':
            message = f"""
⏰ <b>Напоминание о подписке</b>

📅 <b>Ваша {sub_name.lower()} заканчивается через 2 дня!</b>

💡 <b>Не забудьте продлить подписку</b>, чтобы не потерять доступ к сервису.

🔄 <b>Продлите подписку сейчас</b> и продолжайте пользоваться без перерывов!
"""
        elif reminder_type == 'expired':
            # Когда подписка истекла - отправляем уведомление и переключаем пользователя на сквад с ограниченным доступом
            message = f"""
❌ <b>Подписка истекла</b>

📅 <b>Ваша {sub_name.lower()} закончилась.</b>

🔒 <b>Доступ к сервису временно ограничен.</b>

🔄 <b>Продлите подписку</b>, чтобы восстановить доступ и продолжить пользоваться сервисом без ограничений!
"""
            # Отправляем запрос на обновление сквада (переключение на ремнаутрон)
            # Сначала проверяем, не был ли пользователь уже переключен
            from bot_management.models import Payment
            try:
                latest_payment = Payment.objects.filter(
                    user__user_id=user_id,
                    status='succeeded',
                    subscription_type=subscription_type
                ).order_by('-subscription_expires_at').first()
                
                if latest_payment and not latest_payment.switched_to_expired_squad:
                    try:
                        await switch_user_to_expired_squad(user_id)
                        # Помечаем, что пользователь переключен
                        latest_payment.switched_to_expired_squad = True
                        latest_payment.save(update_fields=['switched_to_expired_squad'])
                    except Exception as squad_error:
                        logging.error(f'Ошибка переключения пользователя {user_id} на сквад истекшей подписки: {squad_error}')
                elif latest_payment:
                    logging.info(f'Пользователь {user_id} уже переключен на сквад истёкшей подписки, пропускаем')
            except Exception as e:
                logging.error(f'Ошибка проверки статуса переключения пользователя {user_id}: {e}')
        else:
            return

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Продлить подписку", "callback_data": f"renew_subscription:{subscription_type}"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        # Отправляем сообщение через Telegram Bot API
        from config import BOT_TOKEN
        import requests
        import json

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': user_id,
            'text': message,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        }

        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logging.info(f'Отправлено напоминание о подписке ({reminder_type}) пользователю {user_id}')
            else:
                logging.error(f'Ошибка Telegram API при отправке напоминания: {result.get("description")}')
        else:
            logging.error(f'HTTP ошибка при отправке напоминания: {response.status_code} - {response.text}')

    except Exception as e:
        logging.error(f'Ошибка отправки напоминания о подписке пользователю {user_id}: {e}')

async def send_pending_payment_reminder(user_id: int, subscription_type: str, amount: int):
    """Отправляет напоминание о незавершенном платеже"""
    try:
        from config import BOT_TOKEN

        import requests
        import json

        # Определяем текст напоминания
        sub_names = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }
        sub_name = sub_names.get(subscription_type, 'Подписка')

        message = f"""
⏰ <b>Напоминание о незавершенном платеже</b>

💳 <b>Сумма:</b> {amount} ₽
📅 <b>Подписка:</b> {sub_name}
⏱️ <b>Создан:</b> 30 минут назад

❌ <b>Ваш платеж все еще ожидает оплаты!</b>

💡 <b>Что делать:</b>
• Завершите оплату по инструкции выше
• Или создайте новый платеж, если возникли проблемы

🔄 <b>Хотите создать новый платеж?</b>
"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Создать новый платеж", "callback_data": f"retry_payment:{subscription_type}"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        # Отправляем сообщение через Telegram Bot API
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': user_id,
            'text': message,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        }

        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logging.info(f'Отправлено напоминание о незавершенном платеже пользователю {user_id}')
            else:
                logging.error(f'Ошибка Telegram API при отправке напоминания: {result.get("description")}')
        else:
            logging.error(f'HTTP ошибка при отправке напоминания: {response.status_code} - {response.text}')

    except Exception as e:
        logging.error(f'Ошибка отправки напоминания пользователю {user_id}: {e}')

def get_next_card():
    with get_db() as conn:
        cursor = conn.cursor()
        # Сначала ищем карты с наименьшей загрузкой
        cursor.execute("""
            SELECT * FROM cards 
            WHERE is_active = 1 AND current_received < max_limit
            ORDER BY 
                (current_received * 1.0 / max_limit) ASC,  -- по проценту загрузки
                card_id ASC  -- если проценты равны, то по ID
            LIMIT 1
        """)
        return cursor.fetchone()

def get_available_key(sub_type: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM keys 
            WHERE subscription_type = ? AND used_activations < total_activations AND is_active = 1
            ORDER BY key_id
            LIMIT 1
        """, (sub_type,))
        key = cursor.fetchone()
        if key:
            cursor.execute("""
                UPDATE keys 
                SET used_activations = used_activations + 1,
                    is_active = CASE WHEN used_activations + 1 >= total_activations THEN 0 ELSE 1 END
                WHERE key_id = ?
            """, (key["key_id"],))
            return key
        return None

def get_or_create_support_chat(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM support_chats WHERE user_id = ? AND status = 'open'", (user_id,))
        chat = cursor.fetchone()
        if not chat:
            cursor.execute("INSERT INTO support_chats (user_id, status, created_at, unread_admin_messages, unread_user_messages) VALUES (?, ?, CURRENT_TIMESTAMP, 0, 0)", (user_id, 'open'))
            chat_id = cursor.lastrowid
            return chat_id
        return chat["chat_id"]

async def send_support_response_to_user(user_id: int, message_text: str, photo_file_id: str = None):
    """Отправляет ответ от поддержки пользователю"""
    try:
        logging.info(f"DEBUG: Отправляем ответ поддержки пользователю {user_id}")
        logging.info(f"DEBUG: Текст: {message_text}")
        logging.info(f"DEBUG: Фото: {photo_file_id}")
        
        # Создаем клавиатуру с кнопкой "Ответить"
        reply_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить поддержке", callback_data="reply_to_support")]
        ])
        
        if photo_file_id:
            # Отправляем только фото без текста
            logging.info(f"DEBUG: Отправляем фото без подписи")
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_file_id,
                reply_markup=reply_keyboard
            )
        else:
            # Отправляем текстовое сообщение с изображением поддержки
            text = f"💬 <b>Ответ от поддержки:</b>\n\n{message_text}"
            logging.info(f"DEBUG: Отправляем текст: {text}")
            
            # Используем send_or_edit_message_with_photo для показа изображения поддержки
            from aiogram.types import CallbackQuery
            fake_callback = type('FakeCallback', (), {
                'message': type('FakeMessage', (), {
                    'chat': type('FakeChat', (), {'id': user_id})(),
                    'message_id': None
                })()
            })()
            
            await send_or_edit_message_with_photo(
                fake_callback, 
                text, 
                reply_markup=reply_keyboard, 
                edit_message=False,
                image_name="support.png"
            )
        
        # Сохраняем ответ в базу (БЕЗОПАСНО)
        chat_id = get_or_create_support_chat_safe(user_id)
        if chat_id:
            await save_support_message_safe(chat_id, 'admin', message_text, photo_file_id)
        
        logging.info(f"DEBUG: Ответ поддержки успешно отправлен пользователю {user_id}")
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки ответа поддержки пользователю {user_id}: {e}")
        return False

async def get_user_profile(user_id: int):
    """Получает профиль пользователя"""
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/profile/'
            
            # Используем глобальную сессию для оптимизации
            session = get_http_session()
            async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        logging.info(f"DEBUG: Профиль пользователя {user_id} успешно получен")
                        return data
                    else:
                        error_text = await response.text()
                        logging.error(f"Ошибка получения профиля пользователя {user_id}: HTTP {response.status} - {error_text}")
                        return None
        except aiohttp.ClientError as e:
            logging.error(f"Ошибка сети при получении профиля пользователя {user_id}: {e}")
            return None
        except Exception as e:
            logging.error(f"Ошибка получения профиля пользователя {user_id}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return None
    return None

async def create_balance_payment(user_id: int, amount: float):
    """Создает платеж для пополнения баланса"""
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/balance/deposit/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, data={
                    'user_id': user_id,
                    'amount': amount
                }) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return None
        except Exception as e:
            logging.error(f"Ошибка создания платежа для пополнения баланса: {e}")
            return None
    return None

async def buy_subscription_with_balance(user_id: int, subscription_type: str, price: float):
    """Покупка подписки с баланса"""
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/subscription/buy-with-balance/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json={
                    'user_id': user_id,
                    'subscription_type': subscription_type,
                    'price': price
                }) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return None
        except Exception as e:
            logging.error(f"Ошибка покупки подписки с баланса: {e}")
            return None
    return None


async def refund_balance_payment(user_id: int, amount: float, reason: str = "Возврат при ошибке покупки"):
    """Возврат средств на баланс при ошибке покупки"""
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/balance/refund/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json={
                    'user_id': user_id,
                    'amount': amount,
                    'reason': reason
                }) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('success'):
                            logging.info(f"Возврат {amount} ₽ пользователю {user_id}: {reason}")
                            return True
                    return False
        except Exception as e:
            logging.error(f"Ошибка возврата средств пользователю {user_id}: {e}")
            return False
    return False

async def create_deposit_payment(callback: CallbackQuery, amount: float):
    """Создание платежа для пополнения баланса из callback (новая упрощенная система)"""
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            from bot_management.simple_bot_handlers import create_simple_balance_payment
            
            # Создаем простой платеж для пополнения баланса
            payment_data = await create_simple_balance_payment(user_id, amount)
            
            if payment_data.get('success'):
                payment_id = payment_data.get('payment_id')
                confirmation_url = payment_data.get('confirmation_url')
                
                success_text = f"""
✅ <b>Платеж создан!</b>

💰 <b>Сумма:</b> {amount} ₽
🆔 <b>ID платежа:</b> {payment_id}

🔗 <b>Нажмите кнопку "Оплатить" для перехода к оплате</b>

<i>После оплаты платеж будет обработан автоматически</i>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_simple_payment_{payment_id}")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await send_or_edit_message_with_photo(callback, success_text, reply_markup=kb, edit_message=True, image_name="balance.png")
                
                logging.info(f"Простой платеж {payment_id} создан для пользователя {user_id}")
            else:
                error_text = f"""
❌ <b>Ошибка создания платежа</b>

🔧 <b>Что происходит:</b>
• {payment_data.get('message', 'Временная недоступность платежной системы')}

⏰ <b>Попробуйте через несколько минут</b>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="deposit_balance")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="balance.png")
                
        except Exception as e:
            logging.error(f"Ошибка создания простого платежа пополнения баланса: {e}")
            await send_or_edit_message_with_photo(callback, "❌ Произошла ошибка. Попробуйте позже.", edit_message=True, image_name="balance.png")
    else:
        await send_or_edit_message_with_photo(callback, "❌ Система недоступна. Попробуйте позже.", edit_message=True, image_name="balance.png")

async def create_deposit_payment_from_message(message: Message, amount: float):
    """Создание платежа для пополнения баланса из сообщения"""
    user_id = message.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            # Создаем платеж для пополнения баланса
            payment_data = await create_balance_payment(user_id, amount)
            
            if payment_data and payment_data.get('success'):
                payment_id = payment_data.get('payment_id')
                confirmation_url = payment_data.get('confirmation_url')
                
                success_text = f"""
✅ <b>Платеж создан!</b>

💰 <b>Сумма:</b> {amount} ₽
🆔 <b>ID платежа:</b> {payment_id}

🔗 <b>Нажмите кнопку "Оплатить" для перехода к оплате</b>

<i>После оплаты платеж будет обработан автоматически</i>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_balance_payment_{payment_id}")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await message.answer(success_text, parse_mode="HTML", reply_markup=kb)
                
                # Автоматическая проверка отключена - пользователь должен нажать кнопку "Проверить статус"
                logging.info(f"Платеж {payment_id} создан для пользователя {user_id}, ожидается ручная проверка")
            else:
                error_text = """
❌ <b>Ошибка создания платежа</b>

🔧 <b>Что происходит:</b>
• Временная недоступность платежной системы
• Попробуйте позже

⏰ <b>Попробуйте через несколько минут</b>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="deposit_balance")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await message.answer(error_text, parse_mode="HTML", reply_markup=kb)
                
        except Exception as e:
            logging.error(f"Ошибка создания платежа для пополнения баланса: {e}")
            await message.answer("❌ Произошла ошибка. Попробуйте позже.")
    else:
        await message.answer("❌ Система недоступна. Попробуйте позже.")

# --- Основные хендлеры ---

@router.message(Command("admin"))
@protect_message('messages')
async def cmd_admin(message: Message):
    """Команда /admin - панель администратора"""
    user_id = message.from_user.id
    
    # Проверяем права администратора
    if not is_admin(user_id):
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            # Получаем статистику
            api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        users = data.get('users', {})
                        payments = data.get('payments', {})
                        keys = data.get('keys', {})
                        
                        admin_text = f"""
🔧 <b>Панель администратора</b>

📊 <b>Статистика:</b>

👥 <b>Пользователи:</b>
• Всего: {users.get('total', 0)}
• Сегодня: {users.get('new_today', 0)}
• За неделю: {users.get('new_week', 0)}

💳 <b>Платежи:</b>
• Всего: {payments.get('total', 0)}
• Ожидают: {payments.get('pending', 0)}
• Успешных: {payments.get('succeeded', 0)}
• Выручка: {payments.get('revenue', 0):.2f} ₽
• <b>За сегодня:</b> {payments.get('revenue_today', 0):.2f} ₽

🔑 <b>Ключи:</b>
• Всего: {keys.get('total', 0)}
• Доступно: {keys.get('available', 0)}

<i>Выберите раздел для управления ⬇️</i>
"""
                    else:
                        admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Ошибка загрузки статистики</b>

<i>Попробуйте позже</i>
"""
        except Exception as e:
            logging.error(f"Ошибка получения статистики для админки: {e}")
            admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Ошибка загрузки данных</b>

<i>Попробуйте позже</i>
"""
    else:
        admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Система недоступна</b>

<i>Django интеграция не настроена</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="admin_keys")],
        [InlineKeyboardButton(text="💰 Выплаты", callback_data="payout_menu")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await message.answer(admin_text, parse_mode="HTML", reply_markup=kb)

@router.message(Command("menu"))
@protect_message('messages')
async def cmd_menu(message: Message):
    """Команда /menu - показывает главное меню"""
    welcome_text = """
<b>не хватает скорости?</b>

<b>Мы помогаем оставаться на связи 24/7.</b>

Где бы вы ни были — в любой точке нашей страны🇷🇺.

Неважно, что вы используете — Wi-Fi или мобильные данные.

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

<i>Выберите действие из меню ниже: ⬇️</i>
"""
    
    # Проверяем, включена ли функция "Написать менеджеру"
    manager_key_delivery_enabled = True  # По умолчанию включено
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            value = data.get('value')
                            if value is not None:
                                manager_key_delivery_enabled = str(value).lower() == 'true'
        except:
            pass  # Используем значение по умолчанию
    
    keyboard = [
        [InlineKeyboardButton(text="🔑 Получить ключ", callback_data="get_key_menu")],
    ]
    
    # Добавляем кнопку "Написать менеджеру" если функция включена
    if manager_key_delivery_enabled:
        keyboard.append([InlineKeyboardButton(text="📞 Получить ключ у менеджера", url="https://t.me/yamalube61")])
    
    keyboard.extend([
        [InlineKeyboardButton(text="🔐 Мои ключи", callback_data="my_keys"), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
        [InlineKeyboardButton(text="⭐ Отзывы", url="https://t.me/otzyvywebnetvpn")],
        [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61"), InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")],
    ])

    # Добавляем кнопку админки для администраторов
    if is_admin(message.from_user.id):
        keyboard.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=kb)

@router.message(Command("ban"), F.chat.type == "private")
async def cmd_ban(message: Message):
    """Команда /ban <user_id> — забанить/разбанить пользователя (только админы)"""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    if not args:
        await message.answer("🚫 Использование: /ban <user_id>")
        return
    try:
        target_id = int(args[0].strip())
    except ValueError:
        await message.answer("❌ Неверный ID. Пример: /ban 123456789")
        return
    from bot_management.models import TelegramUser
    from asgiref.sync import sync_to_async
    user = await sync_to_async(TelegramUser.objects.filter(user_id=target_id).first)()
    if not user:
        await message.answer(f"❌ Пользователь {target_id} не найден")
        return
    if user.is_banned:
        user.is_banned = False
        user.ban_reason = ''
        await sync_to_async(user.save)(update_fields=['is_banned', 'ban_reason'])
        await message.answer(f"✅ Разбанен: {user.username or user.user_id} ({target_id})")
    else:
        user.is_banned = True
        user.ban_reason = 'admin ban'
        await sync_to_async(user.save)(update_fields=['is_banned', 'ban_reason'])
        await message.answer(f"🚫 Забанен: {user.username or user.user_id} ({target_id})")


@router.message(Command("verify"))
@rate_limit_check('messages')
async def cmd_verify(message: Message, command: CommandObject = None):
    """Проверить upgradeCode и получить полный VPN ключ"""
    user = message.from_user
    if not command or not command.args:
        await message.answer("❌ Укажите код. Пример: /verify ABC12345\n\nКод можно найти на странице оплаты после покупки.")
        return

    code = command.args.strip()
    from config import SITE_URL
    import aiohttp

    status_msg = await message.answer("⏳ Проверяю код...")

    try:
        # 1. Проверяем код через сайт
        async with aiohttp.ClientSession() as session:
            verify_url = f"{SITE_URL}/api/verify-code?code={code}"
            async with session.get(verify_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text("❌ Код не найден или недействителен. Проверьте правильность ввода.")
                    return
                verify_data = await resp.json()

        if not verify_data.get('valid'):
            await status_msg.edit_text("❌ Код не найден. Проверьте правильность ввода.")
            return

        if verify_data.get('alreadyUpgraded'):
            await status_msg.edit_text("✅ Полный ключ уже активирован! Вернитесь на сайт и нажмите «Проверить статус».")
            return

        plan_type = verify_data.get('planType', 'monthly')
        duration_days = verify_data.get('durationDays', 30)
        full_vpn_type = verify_data.get('fullVpnType', 'night')

        # 2. Проверяем подписку на канал
        await status_msg.edit_text("⏳ Проверяю подписку на канал...")
        try:
            chat_member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user.id)
            if chat_member.status not in ('member', 'administrator', 'creator'):
                await status_msg.edit_text(
                    f"❌ Вы не подписаны на канал {REQUIRED_CHANNEL}.\n"
                    f"Подпишитесь и попробуйте снова: /verify {code}"
                )
                return
        except Exception as e:
            logging.warning(f"Ошибка проверки подписки: {e}")
            await status_msg.edit_text(
                "❌ Не удалось проверить подписку. Возможно, бот не является администратором канала.\n"
                "Попробуйте позже или обратитесь в поддержку."
            )
            return

        # 3. Создаём полный ключ через Remnawave
        await status_msg.edit_text("⏳ Создаю полный ключ...")
        from bot_management.remnawave_api import get_remnawave_bypass_client, RemnawaveAPIError

        bypass_client = get_remnawave_bypass_client()
        if not bypass_client:
            await status_msg.edit_text("❌ Ошибка: VPN сервис временно недоступен. Попробуйте позже.")
            return

        import uuid as uuid_mod
        clean_name = ''.join(c for c in (user.username or f'user{user.id}') if c.isalnum() or c in '_-')[:20]
        if len(clean_name) < 3:
            clean_name = f"usr{clean_name}"
        remnawave_username = f"bp_{clean_name}_{uuid_mod.uuid4().hex[:6]}"

        try:
            remnawave_user = await bypass_client.create_user(
                telegram_id=user.id,
                username=remnawave_username,
                expire_days=duration_days
            )
        except RemnawaveAPIError as e:
            await status_msg.edit_text(f"❌ Ошибка создания ключа: {e}")
            return

        remnawave_user_uuid = remnawave_user.get('uuid')
        if not remnawave_user_uuid:
            await status_msg.edit_text("❌ Ошибка: не удалось получить UUID пользователя")
            return

        # Обновляем expireAt и статус
        from datetime import datetime, timedelta, timezone
        expire_dt = datetime.now(timezone.utc) + timedelta(days=duration_days)
        new_expire_at = expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{expire_dt.microsecond // 1000:03d}Z"

        update_data = {
            'uuid': remnawave_user_uuid,
            'expireAt': new_expire_at,
            'status': 'ACTIVE',
        }
        try:
            updated_user = await bypass_client._request('PATCH', '/api/users', update_data)
            updated_user = updated_user.get('response', {})
        except RemnawaveAPIError as e:
            await status_msg.edit_text(f"❌ Ошибка обновления ключа: {e}")
            return

        subscription_key = updated_user.get('subscriptionUrl') or remnawave_user.get('subscriptionUrl')
        if not subscription_key:
            await status_msg.edit_text("❌ Ошибка: не удалось получить ссылку подписки")
            return

        # 4. Сообщаем сайту о готовом ключе
        await status_msg.edit_text("⏳ Сохраняю ключ...")
        async with aiohttp.ClientSession() as session:
            confirm_url = f"{SITE_URL}/api/confirm-upgrade"
            async with session.post(confirm_url, json={
                'code': code,
                'fullVpnKey': subscription_key,
                'tgId': user.id,
            }) as resp:
                if resp.status != 200:
                    logging.error(f"Ошибка подтверждения апгрейда на сайте: {await resp.text()}")
                    await status_msg.edit_text(
                        "✅ Ключ создан, но не удалось сохранить его на сайте.\n"
                        "Вернитесь на сайт и нажмите «Проверить статус», возможно ключ уже активен."
                    )
                    return

        # 5. Отправляем ключ пользователю
        setup_guide = (
            "🌐 <b>Ваш полный VPN ключ (ночной VPN с обходом блокировок):</b>\n\n"
            f"<code>{subscription_key}</code>\n\n"
            "<b>Как подключиться:</b>\n"
            "1. Скачайте приложение <a href='https://incy.cc/'>INCY</a> (AppStore / PlayMarket)\n"
            "2. Нажмите <b>+ Добавить</b>\n"
            "3. Вставьте ссылку ключа\n"
            "4. Включите VPN\n\n"
            "Готово ✅"
        )
        await message.answer(setup_guide, parse_mode="HTML", disable_web_page_preview=True)
        await status_msg.edit_text(
            f"✅ Полный ключ активирован!\n\n"
            f"Ключ отправлен выше 👆\n"
            f"Также он доступен на сайте в разделе «Проверить статус»."
        )

    except aiohttp.ClientError as e:
        logging.error(f"Ошибка HTTP запроса к сайту: {e}")
        await status_msg.edit_text("❌ Ошибка соединения с сайтом. Попробуйте позже.")
    except Exception as e:
        logging.error(f"Ошибка в /verify: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Неожиданная ошибка: {e}")


@router.message(Command("start"))
@rate_limit_check('messages')
async def cmd_start(message: Message, command: CommandObject = None):
    user = message.from_user
    
    # ВАЖНО: Сначала регистрируем пользователя и обрабатываем реферальный код
    # Это нужно делать ДО проверки подписки, чтобы реферальная система работала
    # даже если пользователь еще не подписан на канал
    
    # Уведомляем Django о новом пользователе
    # ВАЖНО: Передаем данные пользователя, даже если некоторые поля None
    if DJANGO_INTEGRATION:
        user_data = {
            'user_id': user.id,
            'username': user.username if user.username else None,
            'first_name': user.first_name if user.first_name else None,
            'last_name': user.last_name if user.last_name else None
        }
        logging.info(f"DEBUG: Отправка данных пользователя в Django: user_id={user_data['user_id']}, username={user_data['username']}, first_name={user_data['first_name']}")
        await notify_django_new_user(user_data)
    
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        """, (user.id, user.username, user.first_name, user.last_name))
    
    # Обрабатываем реферальный код ДО проверки подписки
    referral_message = ""
    if command and command.args and DJANGO_INTEGRATION:
        try:
            # Используем глобальную сессию для оптимизации
            session = get_http_session()
            
            # Обрабатываем реферал
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/process/'
            async with session.post(api_url, data={
                'referrer_code': command.args,
                'referred_user_id': user.id
            }) as response:
                if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            referrer = data.get('referrer', {})
                            referrer_name = referrer.get('username') or referrer.get('first_name', 'пользователь')
                            referrer_display = f"@{referrer_name}" if referrer.get('username') else referrer_name
                            referral_message = f"\n👥 <b>Вы приглашены пользователем {referrer_display}</b>\n"
                            logging.info(f"DEBUG: Реферальный код обработан успешно для пользователя {user.id}, реферер: {referrer_name}")
                        else:
                            referral_message = f"\n⚠️ <b>Реферальный код недействителен</b>\n"
                            logging.warning(f"DEBUG: Реферальный код недействителен для пользователя {user.id}, код: {command.args}")
        except Exception as e:
            logging.error(f"Ошибка обработки реферального кода: {e}")
            import traceback
            logging.error(traceback.format_exc())
            referral_message = ""
    
    # Проверка подписки на канал отключена
    
    # Автовыдача пробного ключа ускорителя — будет после приветствия с задержкой
    
    # Проверяем специальные параметры команды start
    if command and command.args:
        if command.args.startswith("payment_success_"):
            # Пользователь вернулся после оплаты (Platega)
            payment_id = command.args.replace("payment_success_", "")
            
            # Проверяем, это Обычный VPN или нет
            if DJANGO_INTEGRATION:
                payment_data = {}
                try:
                    import aiohttp
                    api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/detail/'
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(api_url) as response:
                            if response.status == 200:
                                result = await response.json()
                                payment_data = result.get('payment', {})
                                vpn_type = payment_data.get('vpn_type', 'night')
                                subscription_type = payment_data.get('subscription_type', '')
                                issued_key = payment_data.get('issued_key')
                                expires_at = payment_data.get('subscription_expires_at')
                                
                                # Если это Обычный VPN и ключ уже выдан
                                if vpn_type == 'regular' and issued_key:
                                    # Отправляем сообщения как для пробного ключа
                                    expires_str = expires_at[:16].replace('T', ' ') if expires_at else 'неизвестно'
                                    
                                    message1_text = f"""
🎉 <b>Оплата прошла успешно!</b>
✅ <b>Ваша подписка подключена</b>
🎁 <b>Ваш ключ:</b> {issued_key}

<i>Спасибо за покупку! 🚀</i>

📅 <b>Действителен до:</b> {expires_str}

<b>🔑 Как подключить устройство?</b>

📲 <b>Установка и настройка:</b>
1. Скачайте приложение <b>"Happ"</b> (для Обычный VPN)
2. Скопируйте ключ выше (нажмите на ключ → Копировать)
3. В правом верхнем углу нажмите <b>"+"</b> (плюсик)
4. Выберите опцию <b>"Вставить из буфера обмена"</b>
5. Разрешите вставку данных

<i>📌 Ниже отправлена подробная инструкция!</i>
"""

                                    message2_text = f"""
<b>🌐 Инструкция по подключению и настройке</b>

📲 <b>Шаг 1: Выбор сервера</b>

<b>🌍 Выбор сервера от ГЛУШИЛОК:</b>
❗️ При глушении связи — выбирайте сервер с названием вашего оператора либо с припиской <b>ОБХОД</b>
❗️ Если интернет не глушат — используйте обычные сервера в самом низу списка под названием VPN

🔥 <b>КЛЮЧЕВЫЕ ПРАВИЛА:</b>

📶 <b>Если вы подключены через WI-FI:</b>
Выбирайте любые серверы БЕЗ пометки «ОБХОД» и операторов.

📱 <b>Если вы используете МОБИЛЬНЫЙ ИНТЕРНЕТ:</b>
Выбирайте ТОЛЬКО серверы с пометкой «ОБХОД» либо с названием вашего оператора.

---

<b>🌐 ЕСЛИ ПОДКЛЮЧЕНИЕ НЕ РАБОТАЕТ 🔧</b>

1. Нажмите на кнопку обновления (в правом верхнем углу).
2. ⚠️ <b>Избегайте неработающих серверов:</b>
3. ✅ <b>Выбирайте рабочий сервер:</b> с пингом (цифры в ms).
4. 📊 Чем НИЖЕ пинг — тем БЫСТРЕЕ соединение!

---

<b>🔧 Решение проблем:</b>
· Обновить конфигурацию
· Перезапустить приложение
· Включить/выключить VPN
"""

                                    kb1 = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="📋 Скопировать ключ", url=f"tg://msg?text={issued_key}")],
                                        [
                                            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"),
                                            InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")
                                        ]
                                    ])

                                    await send_or_edit_message_with_photo(
                                        message,
                                        message1_text,
                                        reply_markup=kb1,
                                        edit_message=False,
                                        image_name="catalog.png"
                                    )

                                    kb2 = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                                        [InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")]
                                    ])

                                    await message.answer(
                                        message2_text,
                                        reply_markup=kb2,
                                        parse_mode="HTML"
                                    )
                                    return
                except Exception as e:
                    logging.error(f"Ошибка получения данных платежа {payment_id}: {e}")

            # Сразу проверяем статус через Antilopay API
            try:
                import aiohttp
                api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/antilopay-status/'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get('success'):
                                issued_key = data.get('issued_key')
                                is_binding = data.get('is_binding', False)
                                recurrent_error = data.get('recurrent_error', False)

                                if issued_key:
                                    # Показываем ключ сразу (не через _send_key_message — он для CallbackQuery)
                                    vpn_label = {"night": "ОБХОД глушилок + VPN", "regular": "Обычный VPN", "fast": "Обычный VPN"}.get(data.get('vpn_type', 'night'), "VPN")
                                    key_text = f"""
🎉 <b>Оплата подтверждена!</b>

✅ <b>Подписка {vpn_label} активирована</b>

🔑 <b>Ваш ключ:</b>
{issued_key}

📅 <b>Действует до:</b> {data.get('subscription_expires_at', '—')}

<i>Спасибо за покупку! 🚀</i>
"""
                                    await message.answer(key_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🔑 Открыть ключ", url=issued_key)],
                                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"),
                                         InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")]
                                    ]))
                                    return

                                if recurrent_error:
                                    await message.answer(
                                        f"❌ <b>{data.get('message', 'Ошибка автоплатежа')}</b>\n\n"
                                        f"🆔 ID платежа: {payment_id}\n\n"
                                        f"Обратитесь в поддержку @yamalube61",
                                        parse_mode="HTML")
                                    return

                                if not is_binding:
                                    pass
                                else:
                                    # Binding — пробуем выдать пробный ключ
                                    vpn_type = data.get('vpn_type', 'night')
                                    vpn_label = {"night": "ОБХОД глушилок + VPN", "regular": "Обычный VPN", "fast": "Обычный VPN"}.get(vpn_type, "VPN")
                                    try:
                                        from asgiref.sync import sync_to_async
                                        from bot_management.models import Payment
                                        original_payment = await sync_to_async(Payment.objects.get)(payment_id=payment_id)
                                        recurrent_id = original_payment.antilopay_recurrent_id

                                        import aiohttp
                                        trial_api_url = f'{DJANGO_API_URL}/bot_management/api/user/{message.from_user.id}/issue_trial_key/'
                                        async with aiohttp.ClientSession() as session:
                                            async with session.post(trial_api_url, json={'vpn_type': vpn_type, 'antilopay_recurrent_id': recurrent_id}, headers={'Content-Type': 'application/json'}) as trial_resp:
                                                if trial_resp.status == 200:
                                                    trial_data = await trial_resp.json()
                                                    if trial_data.get('success'):
                                                        issued_key = trial_data.get('issued_key')
                                                        regular_vpn_key = trial_data.get('regular_vpn_key')
                                                        expires_at = trial_data.get('expires_at', 'неизвестно')
                                                        trial_hours = 72 if vpn_type == 'night' else 24

                                                        msg_text = f"""🎉 <b>Пробный ключ активирован!</b>

✅ <b>Карта привязана успешно!</b>
"""
                                                        if vpn_type == 'night' and regular_vpn_key:
                                                            msg_text += f"""
🛡️ <b>Ваш ключ ОБХОД глушилок + VPN:</b>
{issued_key}

🌍 <b>Ваш ключ обычного VPN:</b>
{regular_vpn_key}
"""
                                                        else:
                                                            msg_text += f"""
🎁 <b>Ваш ключ:</b> {issued_key}
"""
                                                        msg_text += f"""
📅 <b>Действителен до:</b> {expires_at}
⏰ <b>Пробный период:</b> {trial_hours} ч.
"""
                                                        kb_buttons = [[InlineKeyboardButton(text="🔑 Открыть ключ", url=issued_key)]]
                                                        if regular_vpn_key:
                                                            kb_buttons.append([InlineKeyboardButton(text="🌍 Открыть обычный VPN", url=regular_vpn_key)])
                                                        kb_buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")])

                                                        await message.answer(msg_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

                                                        instruction_text = f"""📲 <b>Установка и настройка</b>

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a>

🙏 <b>УСТАНОВКА</b>
1. Скачиваем приложение <a href="https://incy.cc/">INCY</a> (есть в AppStore и PlayMarket)
2. Нажимаем <b>«+ Добавить»</b>
3. Вставляем ссылку ключа

<b>ГОТОВО ✅</b>

⚠️ <b>Условия использования</b>
· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐 <b>Выбор сервера от ГЛУШИЛОК:</b>
· При глушении связи — выбирайте сервер с припиской <b>ОБХОД БЕЛЫХ СПИСКОВ</b>

❗️ <b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI</b> если хотите чтобы обход заработал ✅
· Если интернет не глушат — используйте обычный VPN

🔒 <b>Безопасность:</b>
· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть заблокирован без возможности возврата средств

⚙️ <b>Решение небольших проблем:</b>
· Обновить конфигурацию (кнопка правее названия «WebNet»)
· Запустить проверку пинга (кнопка молнии, рядом с обновлением)
· Перезапустить приложение
· Включить/выключить VPN
"""
                                                        await send_or_edit_message_with_photo(message, instruction_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                                            [InlineKeyboardButton(text="📲 Скачать INCY", url="https://incy.cc/")],
                                                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                                        ]), edit_message=False, image_name="catalog.png")
                                                        return
                                                    else:
                                                        logging.error(f"Ошибка выдачи пробного ключа: {trial_data.get('error', 'неизвестно')}")
                                                else:
                                                    logging.error(f"HTTP {trial_resp.status} при выдаче пробного ключа")
                                    except Exception as e:
                                        logging.error(f"Ошибка выдачи пробного ключа после привязки: {e}")

                                    await message.answer("⏳ Пробный ключ скоро будет готов. Нажмите «Проверить статус» ещё раз.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_id}")],
                                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                    ]))
                                    return

                                if is_binding:
                                    await message.answer(f"""
🔗 <b>Карта привязана</b>

🆔 ID платежа: {payment_id}

🔄 Нажмите "Проверить статус" для проверки
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_id}")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]))
                                    return
            except Exception as e:
                logging.error(f"Ошибка проверки статуса в payment_success: {e}")

            # Fallback — если API не ответил или неизвестный статус
            await message.answer(f"""
✅ <b>Спасибо за оплату!</b>

💳 Ваш платеж обрабатывается
🆔 ID платежа: {payment_id}

🔄 Нажмите кнопку "Проверить статус" для подтверждения
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_id}")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]))
            return
        elif command.args.startswith("payment_failed_"):
            # Пользователь вернулся после неудачной оплаты (Platega)
            payment_id = command.args.replace("payment_failed_", "")
            await message.answer(f"""
❌ <b>Оплата не завершена</b>

💳 <b>Платеж не был обработан</b>
🆔 <b>ID платежа:</b> {payment_id}

🔄 <b>Попробуйте снова или проверьте статус</b>

<i>Если вы оплатили, но видите это сообщение, нажмите "Проверить статус"</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_id}")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]))
            return
        elif command.args == "payment_success":
            # Пользователь вернулся после оплаты (старая система, без ID)
            await message.answer("""
✅ <b>Спасибо за оплату!</b>

💳 <b>Ваш платеж обрабатывается</b>
🔄 <b>Нажмите кнопку "Проверить статус" для подтверждения</b>

<i>Обычно платеж подтверждается в течение нескольких минут</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]))
            return
        elif command.args == "balance_success":
            # Пользователь вернулся после оплаты пополнения баланса (старая система)
            await message.answer("""
✅ <b>Спасибо за оплату!</b>

💳 <b>Ваш платеж обрабатывается</b>
🔄 <b>Нажмите кнопку "Проверить статус" для подтверждения</b>

<i>Обычно платеж подтверждается в течение нескольких минут</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]))
            return
    
    # Реферальный код уже обработан выше, перед проверкой подписки
    # Если пользователь заходит без реферальной ссылки, устанавливаем способ входа как 'direct'
    if not command or not command.args:
        if DJANGO_INTEGRATION:
            try:
                import aiohttp
                
                # Устанавливаем способ входа как 'direct' для существующих пользователей
                api_url = f'{DJANGO_API_URL}/bot_management/api/user/set-entry-method/'
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, data={
                        'user_id': user.id,
                        'entry_method': 'direct'
                    }) as response:
                        if response.status != 200:
                            print(f"Ошибка установки способа входа: {response.status}")
            except Exception as e:
                print(f"Ошибка установки способа входа: {e}")

    # Inline клавиатура
    keyboard = [
        [InlineKeyboardButton(text="🔑 Получить ключ", callback_data="get_key_menu")],
    ]
    
    # Проверяем, включена ли функция "Получить ключ у менеджера"
    manager_key_delivery_enabled = True  # По умолчанию включено
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            value = data.get('value')
                            if value is not None:
                                manager_key_delivery_enabled = str(value).lower() == 'true'
        except:
            pass  # Используем значение по умолчанию
    
    # Добавляем кнопку "Получить ключ у менеджера" если функция включена
    if manager_key_delivery_enabled:
        keyboard.append([InlineKeyboardButton(text="📞 Получить ключ у менеджера", url="https://t.me/yamalube61")])
    
    keyboard.extend([
        [InlineKeyboardButton(text="🔐 Мои ключи", callback_data="my_keys"), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
        [InlineKeyboardButton(text="⭐ Отзывы", url="https://t.me/otzyvywebnetvpn")],
        [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61"), InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")],
    ])
    
    # Добавляем кнопку админки для администраторов
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    # Обычная клавиатура
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    # Предложение пробного периода перед приветствием (только если триал не использован)
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            trial_check_url = f'{DJANGO_API_URL}/bot_management/api/user/{user.id}/trial_status/?vpn_type=night'
            async with aiohttp.ClientSession() as session:
                async with session.get(trial_check_url) as tr:
                    if tr.status == 200:
                        tr_data = await tr.json()
                        if tr_data.get('success') and not tr_data.get('trial_used') and not tr_data.get('has_active_trial'):
                            trial_offer_text = """🎁 <b>Забери бесплатный пробный период — 3 ДНЯ 🔥</b>

Но нужно пройти верификацию.

<b>Почему мы привязываем счёт и почему это в ваших интересах:</b>

Агенты РКН массово заходят в VPN-боты, получают бесплатный доступ, вычисляют IP серверов и отправляют их на блокировку. Именно так за 3 месяца заблокировали 1069 сервисов.

Верификация привязкой счёта отсекает агентов – они не могут каждый раз создавать новую карту. Результат: наши серверы стабильны, белые списки обходятся без перебоев.

Мы не спишем ничего с этого счёта. После того, как привяжете счёт — вы автоматически получите <b>ПРОБНЫЙ ПЕРИОД — 3 ДНЯ 🔥</b>"""
                            await message.answer(trial_offer_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="💳 Получить пробный доступ", callback_data="trial_offer_get")],
                                [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/yamalube61")]
                            ]))
                            await asyncio.sleep(5)
        except Exception as e:
            logging.warning(f"Ошибка проверки триала при старте: {e}")

    welcome_text = f"""
<b>не хватает скорости?</b>{referral_message}

<b>Мы помогаем оставаться на связи 24/7.</b>

Где бы вы ни были — в любой точке нашей страны🇷🇺.

Неважно, что вы используете — Wi-Fi или мобильные данные.

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

<i>Выберите действие из меню ниже: ⬇️</i>
"""
    
    # Видео отключено - отправляем только основное сообщение с фотографией
    await send_welcome_with_photo(message, welcome_text, reply_markup=kb, reply_keyboard=reply_kb)




@router.callback_query(F.data == "trial_setup_instruction")
async def show_trial_setup_instruction(callback: CallbackQuery):
    """Показывает подробную инструкцию по подключению через Incy"""
    instruction_text = """📲 <b>Установка и настройка</b>

Мы рекомендуем это приложение👇
INCY (<a href='https://incy.cc/'>https://incy.cc/</a>)

🙏 <b>УСТАНОВКА</b>
1. Скачиваем приложение INCY (есть в AppStore и PlayMarket)
2. Нажимаем «+ Добавить»
3. Вставляем ссылку ключа

<b>ГОТОВО ✅</b>

⚠️ <b>Условия использования</b>

· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐 <b>Выбор сервера от ГЛУШИЛОК:</b>

· При глушении связи — выбирайте сервер с припиской «ОБХОД БЕЛЫХ СПИСКОВ»

❗️ <b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI</b> если хотите чтобы обход заработал ✅

· Если интернет не глушат — используйте обычный VPN

🔒 <b>Безопасность:</b>

· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть заблокирован без возможности возврата средств

⚙️ <b>Решение небольших проблем:</b>

· Обновить конфигурацию (кнопка правее названия «WebNet»)
· Запустить проверку пинга (кнопка молнии, рядом с обновлением)
· Перезапустить приложение
· Включить/выключить VPN"""

    await send_or_edit_message_with_photo(
        callback,
        instruction_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ]),
        edit_message=False,
        image_name="catalog.png"
    )


# Проверка подписки на канал (callback)
@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    """Проверка подписки на канал"""
    user = callback.from_user
    
    # ВАЖНО: Убеждаемся, что пользователь зарегистрирован в БД
    # даже если он еще не подписан (на случай если он не был зарегистрирован ранее)
    if DJANGO_INTEGRATION:
        user_data = {
            'user_id': user.id,
            'username': user.username if user.username else None,
            'first_name': user.first_name if user.first_name else None,
            'last_name': user.last_name if user.last_name else None
        }
        await notify_django_new_user(user_data)
    
    await callback.answer("✅ Подписка не требуется")
    await main_menu(callback)

# Выбор подписки через inline кнопки
@router.callback_query(F.data.in_(["sub_night_week", "sub_month", "sub_3months", "sub_6months", "sub_year", 
                                    "sub_regular_day", "sub_regular_month", "sub_regular_3months", 
                                    "sub_regular_6months", "sub_regular_year", "sub_regular_2years",
                                    "sub_fast_week", "sub_fast_month", "sub_fast_3months",
                                    "sub_fast_6months", "sub_fast_year"]))
async def choose_subscription(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора подписки для всех типов VPN"""
    regular_vpn_types = ["sub_regular_day", "sub_regular_month", "sub_regular_3months", 
                         "sub_regular_6months", "sub_regular_year", "sub_regular_2years"]
    
    is_regular_vpn = callback.data in regular_vpn_types
    vpn_type = "regular" if is_regular_vpn else "night"
    
    sub_type_map = {
        "sub_night_week": "week",
        "sub_month": "month",
        "sub_3months": "3months",
        "sub_6months": "6months",
        "sub_year": "year",
        "sub_regular_day": "day",
        "sub_regular_month": "month",
        "sub_regular_3months": "3months",
        "sub_regular_6months": "6months",
        "sub_regular_year": "year",
        "sub_regular_2years": "2years",
    }
    
    sub_type = sub_type_map.get(callback.data, "month")
    
    # Получаем цену в зависимости от типа VPN
    if is_regular_vpn:
        amount = REGULAR_VPN_PRICES.get(sub_type, 0)
    else:
        # Для ОБХОД глушилок + VPN получаем цену из базы данных
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get('prices', {})
                        amount = prices.get(sub_type, PRICES.get(sub_type, 0))
                    else:
                        amount = PRICES.get(sub_type, 0)
        except Exception as e:
            logging.error(f"Ошибка получения цены: {e}")
            amount = PRICES.get(sub_type, 0)

    # Проверка подписки на канал отключена
    
    # Названия подписок
    sub_names_night = {
        'week': '1 неделя',
        'month': 'Месячная подписка',
        '3months': 'Подписка на 3 месяца',
        '6months': 'Подписка на 6 месяцев',
        'year': 'Годовая подписка'
    }
    
    sub_names_regular = {
        'day': '1 день',
        'month': '1 месяц',
        '3months': '3 месяца',
        '6months': '6 месяцев',
        'year': '1 год',
        '2years': '2 года'
    }
    
    sub_names = sub_names_regular if is_regular_vpn else sub_names_night
    sub_name = sub_names.get(sub_type, 'Подписка')
    vpn_label = "Обычный VPN" if is_regular_vpn else "🛡️ ОБХОД глушилок + VPN"

    payment_text = f"""
💳 <b>Выбор способа оплаты</b>

{vpn_label}
📅 <b>Подписка:</b> {sub_name}
💰 <b>Сумма:</b> {amount} ₽

<i>Выберите удобный для вас способ оплаты:</i>
"""

    # Определяем callback_data для возврата
    if is_regular_vpn:
        back_callback = "catalog_regular_vpn"
    else:
        back_callback = "catalog_night_vpn"

    # Проверяем доступен ли CryptoBot
    cryptobot_available = CRYPTOBOT_API_TOKEN and len(CRYPTOBOT_API_TOKEN.strip()) > 0

    kb_buttons = [
        [InlineKeyboardButton(text="📱 СБП (QR-код)", callback_data=f"pay_sbp_{sub_type}_{vpn_type}")],
        [InlineKeyboardButton(text="💳 Банковской картой", callback_data=f"pay_bank_card_{sub_type}_{vpn_type}")],
        [InlineKeyboardButton(text="₿ Криптовалютой (Platega)", callback_data=f"pay_crypto_{sub_type}_{vpn_type}")],
        [InlineKeyboardButton(text="💰 Реферальными средствами", callback_data=f"pay_referral_{sub_type}_{vpn_type}")],
    ]
    
    if cryptobot_available:
        kb_buttons.append([InlineKeyboardButton(text="💎 CryptoBot (TON/USDT)", callback_data=f"pay_cryptobot_{sub_type}_{vpn_type}")])
    
    kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    try:
        await callback.message.edit_text(payment_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(payment_text, parse_mode="HTML", reply_markup=kb)

    await callback.answer()


# Проверка статуса платежа
@router.callback_query(F.data.startswith("check_payment_"))
async def check_payment_status(callback: CallbackQuery):
    """Проверка статуса платежа через CryptoBot или Platega API по payment_id"""
    payment_id = callback.data.split("_")[2]

    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            import json

            # Получаем данные платежа чтобы определить тип
            api_url_basic = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/status/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url_basic) as basic_response:
                    if basic_response.status != 200:
                        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
                        return

                    basic_data = await basic_response.json()
                    platega_id = basic_data.get('platega_transaction_id')
                    cryptobot_id = basic_data.get('cryptobot_invoice_id')
                    antilopay_id = basic_data.get('antilopay_payment_id')
                    payment_status = basic_data.get('status', 'unknown')
                    issued_key = basic_data.get('issued_key')

            # Если ключ уже выдан — отправляем его пользователю повторно
            if payment_status == 'succeeded' and issued_key:
                await _send_key_message(callback, issued_key, basic_data.get('vpn_type', 'night'), basic_data.get('subscription_type', ''))
                return

            # Определяем тип платежа и проверяем только нужный API
            if cryptobot_id:
                # Это платеж через CryptoBot
                api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/cryptobot-status/'
                payment_type = 'cryptobot'
            elif antilopay_id:
                # Это платеж через Antilopay
                api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/antilopay-status/'
                payment_type = 'antilopay'
            elif platega_id:
                # Это платеж через Platega
                api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/platega-status/'
                payment_type = 'platega'
            else:
                await callback.answer("❌ Неизвестный тип платежа. Обратитесь в поддержку", show_alert=True)
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if not data.get('success'):
                            error_message = data.get('message', 'Ошибка проверки статуса')
                            await callback.answer(f"❌ {error_message}", show_alert=True)
                            return

                        if payment_type == 'cryptobot':
                            raw_status = data.get('cryptobot_status', 'unknown')
                            status_normalized = raw_status.lower() if raw_status else 'unknown'
                        elif payment_type == 'antilopay':
                            raw_status = data.get('antilopay_status', 'unknown')
                            status_normalized = raw_status.upper() if raw_status else 'UNKNOWN'
                        else:
                            raw_status = data.get('platega_status', 'unknown')
                            status_normalized = raw_status.upper() if raw_status else 'UNKNOWN'

                        payment_status = data.get('payment_status', 'unknown')
                        issued_key = data.get('issued_key')
                        is_binding = data.get('is_binding', False)
                        recurrent_error = data.get('recurrent_error', False)

                        if recurrent_error:
                            error_msg = data.get('message', 'Ошибка автоплатежа')
                            reject_text = f"❌ <b>Ошибка автоплатежа</b>\n\n{error_msg}\n\nОбратитесь в поддержку для решения проблемы."
                            await send_or_edit_message_with_photo(callback, reject_text,
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="📩 Написать в поддержку", url="https://t.me/webnetvpn_support_bot")],
                                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                ]))
                            return

                        # Проверяем успешную оплату
                        is_paid = (payment_type == 'cryptobot' and status_normalized == 'paid') or \
                                  (payment_type == 'platega' and status_normalized == 'CONFIRMED') or \
                                  (payment_type == 'antilopay' and status_normalized == 'SUCCESS')

                        if is_binding:
                            # Привязка — пробуем выдать пробный ключ
                            vpn_type = data.get('vpn_type', 'night')
                            from asgiref.sync import sync_to_async
                            from bot_management.models import Payment
                            original_payment = await sync_to_async(Payment.objects.get)(payment_id=payment_id)
                            recurrent_id = original_payment.antilopay_recurrent_id
                            import aiohttp
                            trial_api_url = f'{DJANGO_API_URL}/bot_management/api/user/{callback.from_user.id}/issue_trial_key/'
                            async with aiohttp.ClientSession() as session:
                                async with session.post(trial_api_url, json={'vpn_type': vpn_type, 'antilopay_recurrent_id': recurrent_id}, headers={'Content-Type': 'application/json'}) as trial_resp:
                                    if trial_resp.status == 200:
                                        trial_data = await trial_resp.json()
                                        if trial_data.get('success'):
                                            issued_key = trial_data.get('issued_key')
                                            regular_vpn_key = trial_data.get('regular_vpn_key')
                                            expires_at = trial_data.get('expires_at', 'неизвестно')
                                            trial_hours = 72 if vpn_type == 'night' else 24

                                            msg_text = f"""🎉 <b>Пробный ключ активирован!</b>

✅ <b>Карта привязана успешно!</b>
"""
                                            kb_buttons = []
                                            if vpn_type == 'night' and regular_vpn_key:
                                                msg_text += f"""
🛡️ <b>Ваш ключ ОБХОД глушилок + VPN:</b>
{issued_key}

🌍 <b>Ваш ключ обычного VPN:</b>
{regular_vpn_key}
"""
                                                kb_buttons.append([InlineKeyboardButton(text="🛡️ Открыть ОБХОД", url=issued_key),
                                                                   InlineKeyboardButton(text="🌍 Открыть VPN", url=regular_vpn_key)])
                                            else:
                                                msg_text += f"""
🎁 <b>Ваш ключ:</b> {issued_key}
"""
                                                kb_buttons.append([InlineKeyboardButton(text="🔑 Открыть ключ", url=issued_key)])

                                            msg_text += f"""
📅 <b>Действителен до:</b> {expires_at}
⏰ <b>Пробный период:</b> {trial_hours} ч.
"""
                                            kb_buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")])
                                            await send_or_edit_message_with_photo(callback, msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), edit_message=True, image_name="catalog.png")

                                            instruction_text = f"""📲 <b>Установка и настройка</b>

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a>

🙏 <b>УСТАНОВКА</b>
1. Скачиваем приложение <a href="https://incy.cc/">INCY</a> (есть в AppStore и PlayMarket)
2. Нажимаем <b>«+ Добавить»</b>
3. Вставляем ссылку ключа

<b>ГОТОВО ✅</b>

⚠️ <b>Условия использования</b>
· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐 <b>Выбор сервера от ГЛУШИЛОК:</b>
· При глушении связи — выбирайте сервер с припиской <b>ОБХОД БЕЛЫХ СПИСКОВ</b>

❗️ <b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI</b> если хотите чтобы обход заработал ✅
· Если интернет не глушат — используйте обычный VPN

🔒 <b>Безопасность:</b>
· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть заблокирован без возможности возврата средств

⚙️ <b>Решение небольших проблем:</b>
· Обновить конфигурацию (кнопка правее названия «WebNet»)
· Запустить проверку пинга (кнопка молнии, рядом с обновлением)
· Перезапустить приложение
· Включить/выключить VPN
"""
                                            await send_or_edit_message_with_photo(callback, instruction_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                                [InlineKeyboardButton(text="📲 Скачать INCY", url="https://incy.cc/")],
                                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                            ]), edit_message=False, image_name="catalog.png")
                                            return
                                        else:
                                            await callback.answer(f"❌ {trial_data.get('error', 'Ошибка')}", show_alert=True)
                                            return
                                    else:
                                        await callback.answer("❌ Ошибка сервера. Попробуйте позже.", show_alert=True)
                                        return
                        elif is_paid and payment_status == 'succeeded':
                            if issued_key:
                                vpn_type = data.get('vpn_type', 'night')
                                subscription_type = data.get('subscription_type', '')
                                await _send_key_message(callback, issued_key, vpn_type, subscription_type)
                            else:
                                await callback.answer("⏳ Платеж подтвержден, запускаем выдачу ключа...", show_alert=True)
                                await _try_issue_key_on_payment_confirm(payment_id, callback)
                        elif is_paid and payment_status != 'succeeded':
                            await callback.answer("⏳ Платеж подтвержден, запускаем выдачу ключа...", show_alert=True)
                            await _try_issue_key_on_payment_confirm(payment_id, callback)
                        elif status_normalized in ('active', 'pending', 'PENDING'):
                            await callback.answer("⏳ Платеж еще не оплачен. Попробуйте позже.", show_alert=True)
                        elif status_normalized in ('expired', 'CANCELED'):
                            action_text = "истек" if status_normalized == 'expired' else "отменен"
                            await send_or_edit_message_with_photo(callback, f"""
❌ <b>Платеж {action_text}</b>

🔄 <b>Что делать:</b>
• Создайте новый платеж
• Или обратитесь в поддержку

<i>Попробуйте еще раз! 🚀</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🔄 Создать новый платеж", callback_data="catalog")],
                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                            ]))
                        else:
                            await callback.answer(f"❓ Статус платежа: {raw_status}", show_alert=True)
                    elif response.status == 404:
                        await callback.answer("❌ Платеж не найден", show_alert=True)
                    else:
                        error_text = await response.text()
                        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
                        logging.error(f"Ошибка проверки статуса платежа {payment_id}: {response.status} - {error_text}")
        except Exception as e:
            logging.error(f"Ошибка проверки статуса платежа: {e}")
            import traceback
            logging.error(traceback.format_exc())
            await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
    else:
        await callback.answer("❌ Интеграция с платежами недоступна", show_alert=True)

async def _try_issue_key_on_payment_confirm(payment_id: int, callback):
    """Попытка выдать ключ при подтверждении оплаты через check_payment_status"""
    try:
        import aiohttp
        import asyncio

        api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/detail/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await callback.message.answer("❌ Ошибка получения данных платежа")
                    return

                payment_data = await response.json()
                payment_detail = payment_data.get('payment', {})
                vpn_type = payment_detail.get('vpn_type', 'night')
                subscription_type = payment_detail.get('subscription_type', '')
                issued_key = payment_detail.get('issued_key')

                if issued_key:
                    await _send_key_message(callback, issued_key, vpn_type, subscription_type)
                    return

                confirm_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/confirm/'
                async with session.post(confirm_url, json={'payment_id': int(payment_id)}) as confirm_response:
                    try:
                        confirm_data = await confirm_response.json()
                    except Exception:
                        confirm_data = None

                    if confirm_response.status == 200 and confirm_data and confirm_data.get('success'):
                        key = confirm_data.get('issued_key') or confirm_data.get('payment', {}).get('issued_key')
                        if key:
                            await _send_key_message(callback, key, vpn_type, subscription_type)
                        else:
                            await asyncio.sleep(3)
                            async with session.get(api_url) as retry_response:
                                if retry_response.status == 200:
                                    retry_data = await retry_response.json()
                                    retry_key = retry_data.get('payment', {}).get('issued_key')
                                    if retry_key:
                                        await _send_key_message(callback, retry_key, vpn_type, subscription_type)
                                    else:
                                        await callback.message.answer("✅ Платёж подтверждён! Ключ скоро будет отправлен.")
                                else:
                                    await callback.message.answer("✅ Платёж подтверждён! Ключ скоро будет отправлен.")
                    elif confirm_data and not confirm_data.get('success'):
                        error_msg = confirm_data.get('error') or confirm_data.get('message', 'Неизвестная ошибка')
                        await callback.message.answer(f"❌ Ошибка выдачи ключа: {error_msg}\n\nОбратитесь в поддержку: @yamalube61")
                    elif confirm_response.status == 400 and confirm_data:
                        message = confirm_data.get('message', '')
                        if 'уже подтвержден' in message.lower():
                            await asyncio.sleep(2)
                            async with session.get(api_url) as retry_response:
                                if retry_response.status == 200:
                                    retry_data = await retry_response.json()
                                    retry_key = retry_data.get('payment', {}).get('issued_key')
                                    if retry_key:
                                        await _send_key_message(callback, retry_key, vpn_type, subscription_type)
                                    else:
                                        await callback.message.answer("✅ Платёж подтверждён! Ключ скоро будет отправлен.")
                                else:
                                    await callback.message.answer("✅ Платёж подтверждён! Ключ скоро будет отправлен.")
                        else:
                            await callback.message.answer(f"❌ Ошибка выдачи ключа: {confirm_data.get('message', 'Неизвестная ошибка')}")
                    else:
                        await callback.message.answer("❌ Ошибка подтверждения платежа. Обратитесь в поддержку: @yamalube61")

    except Exception as e:
        logging.error(f"Ошибка выдачи ключа при подтверждении оплаты {payment_id}: {e}")
        import traceback
        logging.error(traceback.format_exc())
        await callback.message.answer("❌ Ошибка при выдаче ключа. Обратитесь в поддержку.")


async def _send_key_message(callback, key: str, vpn_type: str, subscription_type: str):
    """Отправляет сообщение с ключом после подтверждения оплаты"""
    try:
        from datetime import timedelta
        from django.utils import timezone

        duration_map = {
            'trial': 1, 'week': 7, 'month': 30, '3months': 90,
            '6months': 180, 'year': 365, '2years': 730,
            'regular_trial': 1, 'regular_day': 1, 'regular_month': 30,
            'regular_3months': 90, 'regular_6months': 180, 'regular_year': 365,
            'fast_trial': 1, 'fast_week': 7, 'fast_month': 30,
            'fast_3months': 90, 'fast_6months': 180, 'fast_year': 365,
        }

        base_type = subscription_type.replace('regular_', '').replace('fast_', '')
        days = duration_map.get(subscription_type, duration_map.get(base_type, 30))
        expires_at = (timezone.now() + timedelta(days=days)).strftime('%d.%m.%Y %H:%M')

        if vpn_type == 'night':
            vpn_label = "ОБХОД глушилок + VPN"
            key_button_text = "🛡️ Открыть ключ"
        elif vpn_type == 'regular':
            vpn_label = "Обычный VPN"
            key_button_text = "⚡ Открыть ключ"
        else:
            vpn_label = "VPN"
            key_button_text = "🔑 Открыть ключ"

        # ===== ПЕРВОЕ СООБЩЕНИЕ: ИНСТРУКЦИЯ (как есть) =====
        instruction_text = f"""
📲 <b>Установка и настройка</b>

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a>

🙏 <b>УСТАНОВКА</b>
1. Скачиваем приложение <a href="https://incy.cc/">INCY</a> (есть в AppStore и PlayMarket)
2. Нажимаем <b>«+ Добавить»</b>
3. Вставляем ссылку ключа

<b>ГОТОВО ✅</b>

⚠️ <b>Условия использования</b>
· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐 <b>Выбор сервера от ГЛУШИЛОК:</b>
· При глушении связи — выбирайте сервер с припиской <b>ОБХОД БЕЛЫХ СПИСКОВ</b>

❗️ <b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI</b> если хотите чтобы обход заработал ✅
· Если интернет не глушат — используйте обычный VPN

🔒 <b>Безопасность:</b>
· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть заблокирован без возможности возврата средств

⚙️ <b>Решение небольших проблем:</b>
· Обновить конфигурацию (кнопка правее названия «WebNet»)
· Запустить проверку пинга (кнопка молнии, рядом с обновлением)
· Перезапустить приложение
· Включить/выключить VPN
"""

        # Отправляем инструкцию
        await callback.message.answer(instruction_text, parse_mode="HTML")

        # ===== ВТОРОЕ СООБЩЕНИЕ: КЛЮЧ С КНОПКАМИ =====
        key_with_buttons = f"""
🎉 <b>Оплата подтверждена!</b>

✅ <b>Подписка {vpn_label} активирована</b>

🔑 <b>Ваш ключ:</b>
{key}

📅 <b>Действует до:</b> {expires_at}

Спасибо за покупку! 🚀
"""

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=key_button_text, url=key)],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"),
             InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")]
        ])

        # Отправляем ключ с кнопками
        await callback.message.answer(key_with_buttons, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        logging.error(f"Ошибка отправки сообщения с ключом: {e}")
        await callback.message.answer(f"🔑 <b>Ваш ключ:</b>\n{key}")


# Вспомогательные функции для работы с ЮKassa API
async def check_yookassa_payment_status(payment_id: int):
    """Проверяет статус платежа через API ЮKassa"""
    try:
        import aiohttp
        
        # Получаем yookassa_payment_id из нашей базы
        api_url = f'{DJANGO_API_URL}/bot_management/api/balance/payment-status/{payment_id}/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    logging.info(f"DEBUG: Получены данные платежа {payment_id}: {data}")
                    
                    if data.get('success', False):
                        # Получаем yookassa_payment_id из базы данных
                        yookassa_payment_id = data.get('yookassa_payment_id')
                        if yookassa_payment_id:
                            logging.info(f"DEBUG: Проверяем статус через ЮKassa для ID: {yookassa_payment_id}")
                            
                            # Проверяем статус через ЮKassa API
                            yookassa_api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{yookassa_payment_id}/yookassa-status/'
                            async with session.post(yookassa_api_url) as yookassa_response:
                                logging.info(f"DEBUG: Запрос к ЮKassa API: {yookassa_api_url}, статус: {yookassa_response.status}")
                                
                                if yookassa_response.status == 200:
                                    yookassa_data = await yookassa_response.json()
                                    logging.info(f"DEBUG: Ответ от ЮKassa API: {yookassa_data}")
                                    return yookassa_data
                                else:
                                    logging.error(f"DEBUG: Ошибка ЮKassa API: {yookassa_response.status}")
                        else:
                            logging.warning(f"DEBUG: yookassa_payment_id не найден для платежа {payment_id}")
                    else:
                        logging.warning(f"DEBUG: API вернул success=False для платежа {payment_id}")
                else:
                    logging.error(f"DEBUG: Ошибка получения данных платежа {payment_id}: {response.status}")
        return None
    except Exception as e:
        logging.error(f"Ошибка проверки статуса через ЮKassa API: {e}")
        return None

async def confirm_payment_in_system(payment_id: int, user_id: int):
    """Подтверждает платеж в нашей системе"""
    try:
        import aiohttp
        
        # Подтверждаем платеж через API
        api_url = f'{DJANGO_API_URL}/bot_management/api/balance/payment-confirm/{payment_id}/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success', False):
                        # Отправляем уведомление пользователю
                        success_text = f"""
✅ <b>Платеж подтвержден!</b>

💰 <b>Сумма:</b> {data.get('amount', 0)} ₽

<i>Спасибо за оплату! 🚀</i>
"""
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                        ])
                        
                        await bot.send_message(
                            chat_id=user_id,
                            text=success_text,
                            parse_mode="HTML",
                            reply_markup=kb
                        )
                        
                        logging.info(f"DEBUG: Платеж {payment_id} автоматически подтвержден через ЮKassa")
                        return True
        return False
    except Exception as e:
        logging.error(f"Ошибка подтверждения платежа {payment_id}: {e}")
        return False

async def handle_canceled_payment(payment_id: int, user_id: int, yookassa_data):
    """Обрабатывает отмененный платеж"""
    try:
        cancel_text = f"""
❌ <b>Платеж отменен</b>

💰 <b>Сумма:</b> {yookassa_data.get('amount', 0)} ₽
🆔 <b>ID платежа:</b> {payment_id}

<i>Попробуйте создать новый платеж</i>
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=cancel_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        
        logging.info(f"DEBUG: Платеж {payment_id} автоматически отменен")
        return True
    except Exception as e:
        logging.error(f"Ошибка обработки отмененного платежа {payment_id}: {e}")
        return False

async def check_yookassa_direct(yookassa_payment_id: str):
    """Прямая проверка статуса платежа через ЮKassa API"""
    try:
        import aiohttp
        
        # Используем наш API endpoint для проверки через ЮKassa
        api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{yookassa_payment_id}/yookassa-status/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    logging.info(f"DEBUG: Прямая проверка ЮKassa для {yookassa_payment_id}: {data}")
                    return data
                else:
                    logging.error(f"DEBUG: Ошибка прямой проверки ЮKassa: {response.status}")
                    return None
    except Exception as e:
        logging.error(f"Ошибка прямой проверки ЮKassa: {e}")
        return None

# Функция автоматической проверки удалена - теперь проверка только по кнопке пользователя

# Проверка статуса простого платежа пополнения баланса
@router.callback_query(F.data.startswith("check_simple_payment_"))
async def check_simple_payment_status(callback: CallbackQuery):
    """Проверка статуса простого платежа пополнения баланса"""
    payment_id = callback.data.split("_")[3]  # check_simple_payment_123 -> 123
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            from bot_management.simple_bot_handlers import check_simple_payment_status as check_status, send_payment_success_message, send_payment_pending_message, send_payment_error_message
            
            # Проверяем статус платежа
            result = await check_status(payment_id, user_id)
            
            if result.get('success'):
                status = result.get('status')
                
                if status == 'succeeded':
                    # Платеж успешен
                    await send_payment_success_message(
                        user_id=user_id,
                        amount=result.get('amount', 0),
                        new_balance=None  # Можно добавить получение баланса
                    )
                elif status == 'pending':
                    # Платеж в ожидании
                    await send_payment_pending_message(
                        user_id=user_id,
                        payment_id=payment_id,
                        amount=result.get('amount', 0)
                    )
                else:
                    # Платеж не прошел
                    await send_payment_error_message(
                        user_id=user_id,
                        message=f"Статус платежа: {status}"
                    )
            else:
                await send_payment_error_message(
                    user_id=user_id,
                    message=result.get('message', 'Ошибка проверки статуса')
                )
                
        except Exception as e:
            logging.error(f"Ошибка проверки статуса простого платежа: {e}")
            await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
    else:
        await callback.answer("❌ Интеграция с платежами недоступна", show_alert=True)

# Проверка статуса платежа пополнения баланса (старая система)
# Обработчик проверки статуса платежа баланса отключен - покупки теперь напрямую
@router.callback_query(F.data.startswith("check_balance_payment_"))
async def check_balance_payment_status(callback: CallbackQuery):
    """Проверка статуса платежа баланса отключена - перенаправление на главное меню"""
    await main_menu(callback)
    return
    
    # Старый код закомментирован - баланс отключен
    # payment_id = callback.data.split("_")[3]  # check_balance_payment_123 -> 123
    # 
    # if DJANGO_INTEGRATION:
    #     try:
    #         import aiohttp
    #         
    #         # Запрос к API для получения статуса платежа
    #         api_url = f'{DJANGO_API_URL}/bot_management/api/balance/payment-status/{payment_id}/'
    #         
    #         async with aiohttp.ClientSession() as session:
    #             async with session.get(api_url) as response:
    #                 if response.status == 200:
    #                     data = await response.json()
    #                     logging.info(f"DEBUG: Ответ API для платежа {payment_id}: {data}")
    #                     
    #                     # Проверяем успешность запроса
    #                     if not data.get('success', False):
    #                         logging.error(f"DEBUG: API вернул success=False для платежа {payment_id}: {data}")
    #                         await callback.answer("❌ Ошибка получения статуса платежа", show_alert=True)
    #                         return
    #                     
    #                     status = data.get('status', 'unknown')
    #                     logging.info(f"DEBUG: Статус платежа {payment_id}: {status}")
    #                     
    #                     if status == 'succeeded':
    #                         # Платеж успешен
    #                         await send_or_edit_message_with_photo(callback, """
    # ✅ <b>Платеж подтвержден!</b>
    # 
    # 💰 <b>Баланс пополнен на:</b> {} ₽
    # 💳 <b>Текущий баланс:</b> {} ₽
    # 
    # <i>Спасибо за пополнение! 🚀</i>
    # """.format(data.get('amount', 0), data.get('new_balance', 0)), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    #                             [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
    #                             [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    #                         ]), image_name="balance.png")
    #                     elif status == 'pending':
    #                         # Платеж в ожидании - проверяем через ЮKassa API
    #                         yookassa_payment_id = data.get('yookassa_payment_id')
    #                         if yookassa_payment_id:
    #                             # Проверяем статус через ЮKassa API
    #                             yookassa_status = await check_yookassa_direct(yookassa_payment_id)
    #                             if yookassa_status and yookassa_status.get('status') in ['succeeded', 'waiting_for_capture']:
    #                                 # Платеж успешен в ЮKassa, подтверждаем в нашей системе
    #                                 await confirm_payment_in_system(int(payment_id), callback.from_user.id)
    #                                 
    #                                 # Отправляем сообщение об успехе
    #                                 await send_or_edit_message_with_photo(callback, """
    # ✅ <b>Платеж подтвержден!</b>
    # 
    # 💰 <b>Баланс пополнен на:</b> {} ₽
    # 💳 <b>Текущий баланс:</b> {} ₽
    # 
    # <i>Спасибо за пополнение! 🚀</i>
    # """.format(data.get('amount', 0), data.get('new_balance', 0)), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    #                                     [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
    #                                     [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    #                                 ]), image_name="balance.png")
    #                                 return
    #                         
    #                         # Если платеж все еще pending
    #                         await send_or_edit_message_with_photo(callback, """
    # ⏳ <b>Платеж в обработке</b>
    # 
    # 💰 <b>Сумма:</b> {} ₽
    # 🆔 <b>ID платежа:</b> {}
    # 
    # <i>Платеж обрабатывается, попробуйте через несколько минут</i>
    # """.format(data.get('amount', 0), payment_id), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    #                             [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"check_balance_payment_{payment_id}")],
    #                             [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    #                         ]), image_name="balance.png")
    #                     else:
    #                         # Платеж не прошел
    #                         await send_or_edit_message_with_photo(callback, """
    # ❌ <b>Платеж не прошел</b>
    # 
    # 💰 <b>Сумма:</b> {} ₽
    # 🆔 <b>ID платежа:</b> {}
    # 📊 <b>Статус:</b> {}
    # 
    # <i>Попробуйте создать новый платеж</i>
    # """.format(data.get('amount', 0), payment_id, status), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    #                             [InlineKeyboardButton(text="💳 Пополнить снова", callback_data="deposit_balance")],
    #                             [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    #                         ]), image_name="balance.png")
    #                 else:
    #                     await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
    #     except Exception as e:
    #         logging.error(f"Ошибка проверки статуса платежа пополнения баланса: {e}")
    #         await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
    # else:
    #     await callback.answer("❌ Интеграция с платежами недоступна", show_alert=True)


# Приём PDF
@router.message(F.content_type == ContentType.DOCUMENT)
@protect_message('messages')
async def handle_pdf(message: Message):
    if not message.document.mime_type == "application/pdf":
        await message.answer("""
❌ <b>Неверный формат файла!</b>

📎 <b>Пожалуйста, отправьте именно PDF-файл:</b>
• Не скриншот экрана
• Не фотографию чека
• А именно PDF-документ

💡 <b>Как получить PDF:</b>
• Скачайте чек из банковского приложения
• Или попросите банк отправить чек на email
""")
        return

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM payments 
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
        """, (message.from_user.id,))
        payment = cursor.fetchone()

        if not payment:
            await message.answer("""
❌ <b>Активный платеж не найден!</b>

🔄 <b>Что делать:</b>
1️⃣ Нажмите /start
2️⃣ Выберите подписку
3️⃣ Получите данные для оплаты
4️⃣ Сделайте перевод
5️⃣ Отправьте PDF-чек

<i>Платеж должен быть активным для загрузки чека</i>
""")
            return

        cursor.execute("""
            UPDATE payments SET pdf_file_id = ?, has_receipt = 1 WHERE payment_id = ?
        """, (message.document.file_id, payment["payment_id"]))

    # Уведомляем Django о загруженном чеке
    if DJANGO_INTEGRATION:
        await notify_django_payment_receipt(payment["payment_id"], message.document.file_id)

    # Уведомление админам
    if payment['subscription_type'] == 'trial':
        sub_type_text = "🎁 Пробная"
    elif payment['subscription_type'] == 'month':
        sub_type_text = "📅 Месячная"
    elif payment['subscription_type'] == '3months':
        sub_type_text = "📅 3 месяца"
    elif payment['subscription_type'] == '6months':
        sub_type_text = "📅 6 месяцев"
    elif payment['subscription_type'] == 'year':
        sub_type_text = "📅 Годовая"
    else:
        sub_type_text = payment['subscription_type']
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"""
📥 <b>Новый платеж требует подтверждения</b>

👤 <b>Пользователь:</b> @{message.from_user.username or message.from_user.id}
💰 <b>Сумма:</b> <code>{payment['amount']} ₽</code>
🎫 <b>Тип подписки:</b> {sub_type_text}
💳 <b>ID карты:</b> <code>{payment['card_id']}</code>

📎 <b>Чек загружен и готов к проверке</b>
""",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👁 Посмотреть чек", callback_data=f"view_pdf:{payment['payment_id']}")],
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{payment['payment_id']}"),
                     InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{payment['payment_id']}")]
                ])
            )
        except:
            pass

    await message.answer("""
✅ <b>Чек успешно получен!</b>

📋 <b>Что происходит дальше:</b>
1️⃣ Администратор проверит ваш чек
2️⃣ Сверит сумму и получателя
3️⃣ Подтвердит или отклонит платеж
4️⃣ При подтверждении вы получите ключ

⏰ <b>Время обработки:</b> обычно в течение 15-30 минут

<i>Спасибо за покупку! 🎉</i>
""")

# Поддержка
@router.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext):
    # Принудительно сбрасываем состояние перед началом поддержки
    await state.clear()
    print(f"DEBUG: Состояние сброшено перед началом поддержки для {callback.from_user.id}")
    
    chat_id = get_or_create_support_chat(callback.from_user.id)
    print(f"DEBUG: Создан/найден чат поддержки с ID: {chat_id}")
    await state.set_state(SupportState.in_chat)
    await state.set_data({"chat_id": chat_id})
    print(f"DEBUG: Установлено состояние поддержки для {callback.from_user.id}")
    
    support_text = """
🛠 <b>Служба поддержки</b>

💬 <b>Мы готовы помочь!</b>

📝 <b>Опишите вашу проблему:</b>
• Проблемы с оплатой
• Вопросы по подписке
• Технические неполадки
• Другие вопросы

⏰ <b>Время ответа:</b> обычно в течение 15-30 минут

<i>Напишите ваш вопрос ниже ⬇️</i>
"""
    
    support_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, support_text, reply_markup=support_kb, edit_message=True)

# Обработчики состояний должны быть выше других обработчиков сообщений
@router.message(BroadcastState.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработка текста рассылки"""
    logging.info(f"DEBUG: Получено сообщение для рассылки от {message.from_user.id}, текст: {(message.text or message.caption or '')[:50]}")
    user_id = message.from_user.id
    
    # Проверяем текущее состояние
    current_state = await state.get_state()
    logging.info(f"DEBUG: Текущее состояние: {current_state}, ожидается: {BroadcastState.waiting_message}")
    
    if current_state != BroadcastState.waiting_message:
        logging.warning(f"DEBUG: Состояние не соответствует! Ожидалось: {BroadcastState.waiting_message}, получено: {current_state}")
        # Все равно обрабатываем, если это админ
        if not is_admin(user_id):
            return
    
    if not is_admin(user_id):
        logging.warning(f"Попытка доступа к рассылке от не-админа {user_id}")
        await message.answer("❌ У вас нет доступа")
        await state.clear()
        return
    
    # Проверяем отмену
    if message.text and message.text.strip() == "/cancel":
        logging.info(f"Рассылка отменена пользователем {user_id}")
        await message.answer("❌ Рассылка отменена")
        await state.clear()
        return
    
    raw_text = message.text or message.caption or ""
    
    if not raw_text.strip():
        logging.warning(f"Пустое сообщение для рассылки от {user_id}")
        await message.answer("❌ Текст сообщения не может быть пустым. Введите текст или отправьте /cancel для отмены.")
        return

    # Парсим текст и кнопки
    lines = raw_text.splitlines()
    text_lines = []
    buttons = []  # список словарей: {'text': ..., 'url': ...} или {'text': ..., 'callback_data': ...}
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("btn:"):
            payload = stripped[4:].strip()
            if not payload:
                continue
            if "|" in payload:
                title, data = [p.strip() for p in payload.split("|", 1)]
            else:
                title, data = payload, payload

            if not title or not data:
                continue

            # URL-кнопка или callback-кнопка
            if data.startswith("http://") or data.startswith("https://"):
                buttons.append({"text": title, "url": data})
            else:
                buttons.append({"text": title, "callback_data": data})
        else:
            text_lines.append(line)

    message_text = "\n".join(text_lines).strip()

    if not message_text:
        logging.warning(f"Пустой текст после парсинга кнопок для рассылки от {user_id}")
        await message.answer("❌ Текст сообщения не может быть пустым. Удалите строки с кнопками или добавьте текст выше.")
        return

    logging.info(f"Обработка сообщения для рассылки: {message_text[:50]}...")
    
    # Сохраняем медиа, если есть
    photo_file_id = None
    video_file_id = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
        await state.update_data(photo_file_id=photo_file_id)
    if message.video:
        video_file_id = message.video.file_id
        await state.update_data(video_file_id=video_file_id)
    
    # Получаем количество пользователей
    user_count = 0
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        user_count = data.get('users', {}).get('total', 0)
        except Exception as e:
            logging.error(f"Ошибка получения статистики: {e}")
    else:
        with get_db() as conn:
            result = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()
            user_count = result['count'] if result else 0
    
    # Сохраняем данные
    await state.update_data(message_text=message_text, user_count=user_count, buttons=buttons)
    
    # Показываем превью
    preview_text = f"""
📢 <b>Предварительный просмотр рассылки</b>

👥 <b>Получателей:</b> <code>{user_count}</code> пользователей

📝 <b>Текст сообщения:</b>
{message_text}

"""
    if buttons:
        preview_text += "\n🔘 <b>Кнопки:</b>\n"
        for b in buttons:
            if 'url' in b:
                preview_text += f"• {b['text']} → {b['url']}\n"
            else:
                preview_text += f"• {b['text']} (callback: {b['callback_data']})\n"

    preview_text += """

⚠️ <b>Внимание:</b> Рассылка начнется сразу после подтверждения!
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить рассылку", callback_data="confirm_broadcast")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_broadcast")]
    ])
    
    if video_file_id:
        await message.answer_video(
            video=video_file_id,
            caption=preview_text,
            parse_mode="HTML",
            reply_markup=kb
        )
    elif photo_file_id:
        await message.answer_photo(
            photo=photo_file_id,
            caption=preview_text,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        await message.answer(preview_text, parse_mode="HTML", reply_markup=kb)
    
    await state.set_state(BroadcastState.waiting_confirmation)

@router.message(AdminState.waiting_user_id)
async def admin_handle_user_id(message: Message, state: FSMContext):
    """Обработчик ввода ID пользователя для админки и других действий"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет доступа")
        await state.clear()
        return
    
    # Проверяем отмену
    if message.text and message.text.strip() == "/cancel":
        await message.answer("❌ Операция отменена")
        await state.clear()
        return
    
    # Проверяем, какое действие выполняется
    data = await state.get_data()
    action = data.get('action')
    
    if action == 'edit_price':
        subscription_type = data.get('subscription_type')
        price_text = message.text.strip()
        
        try:
            price = int(price_text)
            if price <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            await message.answer("❌ Цена должна быть положительным целым числом. Попробуйте снова или отправьте /cancel")
            return
        
        # Обновляем цену
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/update/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json={
                    'subscription_type': subscription_type,
                    'price': price
                }) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('success'):
                            await message.answer(f"✅ Цена для {subscription_type} обновлена на {price} ₽")
                        else:
                            await message.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}")
                    else:
                        error_text = await response.text()
                        await message.answer(f"❌ Ошибка сервера: {error_text}")
        except Exception as e:
            logging.error(f"Ошибка обновления цены: {e}")
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        await state.clear()
        return
    
    elif action == 'edit_name':
        subscription_type = data.get('subscription_type')
        name = message.text.strip()
        
        if not name:
            await message.answer("❌ Название не может быть пустым. Попробуйте снова или отправьте /cancel")
            return
        
        # Обновляем название
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/subscription/name/update/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json={
                    'subscription_type': subscription_type,
                    'name': name
                }) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('success'):
                            await message.answer(f"✅ Название для {subscription_type} обновлено на \"{name}\"")
                        else:
                            await message.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}")
                    else:
                        error_text = await response.text()
                        await message.answer(f"❌ Ошибка сервера: {error_text}")
        except Exception as e:
            logging.error(f"Ошибка обновления названия: {e}")
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        await state.clear()
        return
    
    # Обработка поиска пользователя (оригинальная логика)
    search_text = message.text.strip()
    
    if not DJANGO_INTEGRATION:
        await message.answer("❌ Система недоступна")
        await state.clear()
        return

    try:
        import aiohttp

        # Определяем, ID это или username
        if search_text.startswith('@'):
            # Поиск по username
            username = search_text[1:]  # Убираем @
            api_url = f'{DJANGO_API_URL}/bot_management/api/users/by_username/{username}/profile/'
        else:
            # Поиск по ID
            try:
                search_user_id = int(search_text)
            except ValueError:
                await message.answer("❌ Пожалуйста, введите корректный ID пользователя (число) или username (начинается с @)")
                await state.clear()
                return
            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{search_user_id}/profile/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('success'):
                        user_info = data.get('user', {})
                        first_name = user_info.get('first_name', 'Не указано')
                        last_name = user_info.get('last_name') or ''
                        if last_name and last_name != 'None' and last_name.strip():
                            full_name = f"{first_name} {last_name}"
                        else:
                            full_name = first_name

                        username = user_info.get('username', 'Не указан')
                        created_at = user_info.get('created_at', 'Неизвестно')
                        referral_code = user_info.get('referral_code', 'Не создан')
                        referrals_count = user_info.get('referrals_count', 0)
                        referral_balance = user_info.get('referral_balance', 0)

                        user_text = f"""
👤 <b>Профиль пользователя</b>

🆔 <b>ID:</b> {user_info.get('user_id', search_user_id if 'search_user_id' in locals() else 'Не указан')}
👤 <b>Имя:</b> {full_name}
📱 <b>Username:</b> @{username}
📅 <b>Дата регистрации:</b> {created_at}

👥 <b>Реферальная программа:</b>
🎯 <b>Код:</b> {referral_code}
📊 <b>Приглашено:</b> {referrals_count}
💰 <b>Реферальный баланс:</b> {referral_balance} ₽
"""
                    else:
                        user_text = f"❌ Пользователь не найден"
                else:
                    user_text = f"❌ Пользователь не найден"
    except Exception as e:
        logging.error(f"Ошибка получения профиля пользователя: {e}")
        user_text = "❌ Ошибка загрузки данных"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти другого", callback_data="admin_find_user")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])

    await message.answer(user_text, parse_mode="HTML", reply_markup=kb)
    await state.clear()

@router.message(SupportState.in_chat)
async def support_message(message: Message, state: FSMContext):
    print(f"DEBUG: Получено сообщение в поддержку от {message.from_user.id}")
    print(f"DEBUG: Текущее состояние: {await state.get_state()}")
    
    data = await state.get_data()
    chat_id = data.get("chat_id")
    if not chat_id:
        print("DEBUG: chat_id не найден в состоянии!")
        await message.answer("❌ Ошибка: не удалось найти чат поддержки. Попробуйте начать заново.")
        await state.clear()
        return
    
    # Обрабатываем фото в поддержку
    if message.photo:
        print(f"DEBUG: Получено фото в поддержку от {message.from_user.id}")
        photo = message.photo[-1]
        
        # Сохраняем сообщение с фото в базу (БЕЗОПАСНО)
        await save_support_message_safe(chat_id, 'user', message.caption or "📸 Фото", photo.file_id)
        
        # Уведомляем только в группу поддержки
        if SUPPORT_GROUP_ID:
            try:
                # Форматируем имя пользователя - если есть фамилия, добавляем её
                if message.from_user.username:
                    user_info = f"@{message.from_user.username}"
                else:
                    first_name = message.from_user.first_name or ''
                    last_name = message.from_user.last_name or ''
                    if last_name and last_name != 'None' and last_name.strip():
                        user_info = f"{first_name} {last_name}"
                    elif first_name:
                        user_info = first_name
                    else:
                        user_info = f"ID{message.from_user.id}"
                
                await bot.send_photo(
                    SUPPORT_GROUP_ID,
                    photo=photo.file_id,
                    caption=f"""
📸 <b>Фото от пользователя в поддержку</b>

👤 <b>Пользователь:</b> {user_info}
🆔 <b>ID чата:</b> {chat_id}
📝 <b>Подпись:</b> {message.caption or 'Без подписи'}

<i>Ответьте пользователю через админ панель</i>
""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Ошибка уведомления в группу поддержки: {e}")
        
        # Подтверждаем получение фото
        await message.answer("""
✅ <b>Фото отправлено в поддержку!</b>

⏰ <b>Время ответа:</b> обычно в течение 15-30 минут

💡 <b>Пока ждете:</b>
• Проверьте раздел "Часто задаваемые вопросы"
• Убедитесь, что проблема не решается самостоятельно

<i>Спасибо за обращение! 🙏</i>
""")
        
        # Уведомления в группу уже отправлены выше - дублирование убрано
        print(f"DEBUG: Фото пользователя обработано, сбрасываем состояние")
        await state.clear()
        return
    
    # Уведомляем Django о сообщении поддержки (сохраняет в Django БД и отправляет уведомления)
    if DJANGO_INTEGRATION:
        print(f"DEBUG: Отправляем сообщение в Django для пользователя {message.from_user.id}")
        django_chat_id = await notify_django_support_message(message.from_user.id, message.text)
        print(f"DEBUG: Django вернул chat_id: {django_chat_id}")
        if django_chat_id:
            # Django уже сохранил сообщение и отправил уведомления - ничего больше не делаем
            pass
        else:
            # Если Django не сработал, сохраняем в SQLite и отправляем уведомления
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO support_messages (chat_id, sender, text, sent_at, is_read)
                    VALUES (?, 'user', ?, CURRENT_TIMESTAMP, 0)
                """, (chat_id, message.text))
            
            # Отправляем уведомление в группу
            if SUPPORT_GROUP_ID:
                try:
                    admin_url = f"http://127.0.0.1:8123/bot_management/support/{chat_id}/"
                    group_message = f"""
🚨 <b>Новое сообщение в поддержке!</b>

👤 <b>Пользователь:</b> @{message.from_user.username or message.from_user.first_name or 'Без имени'}
🆔 <b>ID:</b> {message.from_user.id}
📝 <b>Сообщение:</b> {message.text}

🔗 <b>Открыть в админке:</b> <a href="{admin_url}">Перейти к чату</a>
                    """
                    
                    await bot.send_message(
                        SUPPORT_GROUP_ID,
                        group_message,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления в группу: {e}")
    else:
        # Если Django недоступен, сохраняем только в SQLite и отправляем уведомления
        with get_db() as conn:
            conn.execute("""
                INSERT INTO support_messages (chat_id, sender, text, sent_at, is_read)
                VALUES (?, 'user', ?, CURRENT_TIMESTAMP, 0)
            """, (chat_id, message.text))
        
        # Отправляем уведомление в группу
        if SUPPORT_GROUP_ID:
            try:
                admin_url = f"http://127.0.0.1:8123/bot_management/support/{chat_id}/"
                group_message = f"""
🚨 <b>Новое сообщение в поддержке!</b>

👤 <b>Пользователь:</b> @{message.from_user.username or message.from_user.first_name or 'Без имени'}
🆔 <b>ID:</b> {message.from_user.id}
📝 <b>Сообщение:</b> {message.text}

🔗 <b>Открыть в админке:</b> <a href="{admin_url}">Перейти к чату</a>
                """
                
                await bot.send_message(
                    SUPPORT_GROUP_ID,
                    group_message,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления в группу: {e}")
    
    # Подтверждаем пользователю
    await message.answer("""
✅ <b>Сообщение отправлено!</b>

📨 <b>Ваш вопрос передан администратору</b>

⏰ <b>Ожидайте ответа в течение 15-30 минут</b>

💡 <b>Пока ждете:</b>
• Проверьте раздел "Часто задаваемые вопросы"
• Убедитесь, что проблема не решается самостоятельно

<i>Спасибо за обращение! 🙏</i>
    """)
    
    # Уведомления в группу уже отправлены выше - дублирование убрано
    print(f"DEBUG: Сообщение пользователя обработано, сбрасываем состояние")
    await state.clear()

# Обработчики текстовых сообщений
@router.message(F.text == "Меню")
async def handle_menu_button(message: Message):
    """Обработчик кнопки меню - делает то же самое, что и /start"""
    user = message.from_user
    
    # Проверка подписки на канал отключена
    
    # Уведомляем Django о новом пользователе
    # ВАЖНО: Передаем данные пользователя, даже если некоторые поля None
    if DJANGO_INTEGRATION:
        user_data = {
            'user_id': user.id,
            'username': user.username if user.username else None,
            'first_name': user.first_name if user.first_name else None,
            'last_name': user.last_name if user.last_name else None
        }
        logging.info(f"DEBUG: Отправка данных пользователя в Django: user_id={user_data['user_id']}, username={user_data['username']}, first_name={user_data['first_name']}")
        await notify_django_new_user(user_data)
    
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        """, (user.id, user.username, user.first_name, user.last_name))
    
    # Inline клавиатура
    keyboard = [
        [InlineKeyboardButton(text="🔑 Получить ключ", callback_data="get_key_menu")],
    ]
    
    # Проверяем, включена ли функция "Получить ключ у менеджера"
    manager_key_delivery_enabled = True  # По умолчанию включено
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            value = data.get('value')
                            if value is not None:
                                manager_key_delivery_enabled = str(value).lower() == 'true'
        except:
            pass  # Используем значение по умолчанию
    
    # Добавляем кнопку "Получить ключ у менеджера" если функция включена
    if manager_key_delivery_enabled:
        keyboard.append([InlineKeyboardButton(text="📞 Получить ключ у менеджера", url="https://t.me/yamalube61")])
    
    keyboard.extend([
        [InlineKeyboardButton(text="🔐 Мои ключи", callback_data="my_keys"), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
        [InlineKeyboardButton(text="⭐ Отзывы", url="https://t.me/otzyvywebnetvpn")],
        [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61"), InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")],
    ])
    
    # Добавляем кнопку админки для администраторов
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    # Обычная клавиатура
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    welcome_text = """
<b>не хватает скорости?</b>

<b>Мы помогаем оставаться на связи 24/7.</b>

Где бы вы ни были — в любой точке нашей страны🇷🇺.

Неважно, что вы используете — Wi-Fi или мобильные данные.

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

<i>Выберите действие из меню ниже: ⬇️</i>
"""
    
    # Отправляем с фотографией
    await send_welcome_with_photo(message, welcome_text, reply_markup=kb, reply_keyboard=reply_kb)

# Обработчики inline кнопок
@router.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    """Главное меню бота"""
    user_id = callback.from_user.id
    welcome_text = """
<b>не хватает скорости?</b>

<b>Мы помогаем оставаться на связи 24/7.</b>

Где бы вы ни были — в любой точке нашей страны🇷🇺.

Неважно, что вы используете — Wi-Fi или мобильные данные.

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

<i>Выберите действие из меню ниже: ⬇️</i>
"""
    
    # Проверяем, включена ли функция "Написать менеджеру"
    manager_key_delivery_enabled = True  # По умолчанию включено
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            value = data.get('value')
                            if value is not None:
                                manager_key_delivery_enabled = str(value).lower() == 'true'
        except:
            pass  # Используем значение по умолчанию
    
    # Формируем клавиатуру
    keyboard = [
        [InlineKeyboardButton(text="🔑 Получить ключ", callback_data="get_key_menu")],
    ]

    # Добавляем кнопку "Написать менеджеру" если функция включена
    if manager_key_delivery_enabled:
        keyboard.append([InlineKeyboardButton(text="📞 Получить ключ у менеджера", url="https://t.me/yamalube61")])

    keyboard.extend([
        [InlineKeyboardButton(text="🔐 Мои ключи", callback_data="my_keys"), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
        [InlineKeyboardButton(text="⭐ Отзывы", url="https://t.me/otzyvywebnetvpn")],
        [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61"), InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")],
    ])

    # Добавляем кнопку админки для администраторов
    if is_admin(user_id):
        keyboard.append([
            InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel"),
            InlineKeyboardButton(text="📊 Обычный VPN", callback_data="admin_regular_vpn_stats")
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    # Отправляем с фотографией через callback
    await send_welcome_with_photo_callback(callback, welcome_text, reply_markup=kb)


@router.callback_query(F.data == "tg_proxy")
async def tg_proxy_info(callback: CallbackQuery):
    """Информация о прокси для Telegram"""
    text = """
📌 <b>Прокси для Telegram</b>

✅ <b>Белые списки:</b>
1. tg://proxy?server=51.250.89.205&port=443&secret=ee248c43b7aee97c6fe887311452a323026164732e78352e7275
2. tg://proxy?server=84.201.179.235&port=443&secret=ee248c43b7aee97c6fe887311452a323026164732e78352e7275
3. tg://proxy?server=176.109.105.129&port=443&secret=ee248c43b7aee97c6fe887311452a323026164732e78352e7275

🔓 <b>Без белых списков:</b>
1. tg://proxy?server=95.174.92.184&port=443&secret=ee85e493328b7a0d1b2af6c4b0fd948a506164732e78352e7275
2. tg://proxy?server=87.242.100.25&port=443&secret=eefa41d7ab59231ab714b7865c9016a1356164732e78352e7275

🔑 <b>Для белых списков (Socks5):</b>
1. https://t.me/socks?server=109.120.191.248&port=1080&user=tgproxy&pass=xVGavfDim6nxSvby
2. https://t.me/socks?server=109.120.189.122&port=1080&user=tgproxy&pass=VKRecaXEinjq3M9U

⚠️ <b>P.S.</b> Прокси не работают вместе с VPN, поэтому либо настройте исключение на Telegram в вашем клиенте, или отключайте VPN во время использования Telegram.

💡 В исключение идут конфиги Socks5 — они работают совместно с VPN.
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
    ])

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "about")
async def about_service(callback: CallbackQuery):
    about_text = """
ℹ️ <b>О нашем сервисе</b>

<b>Мы помогаем оставаться на связи 24/7.</b>

Где бы вы ни были — в любой точке нашей страны🇷🇺.

Неважно, что вы используете — Wi-Fi или мобильные данные.

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

💎 <b>Типы подписок:</b>
• 📅 <b>1 месяц</b> - доступ на 30 дней
• 📅 <b>3 месяца</b> - доступ на 90 дней
• 📅 <b>12 месяцев</b> - доступ на год

🔒 <b>Безопасность:</b>
• Все платежи обрабатываются автоматически
• Защищенные реквизиты
• Гарантия качества

<i>Выберите подходящую подписку и начните пользоваться сервисом!</i>
"""
    
    about_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"),
        InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61"),
        ],
        [
        InlineKeyboardButton(text="📄 Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-02-11-27"),
        ],
        [
        InlineKeyboardButton(text="📋 Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-06-24-15"),
        ],
        [
        InlineKeyboardButton(text="📋 Рекуррентные платежи", url="https://telegra.ph/Rekurrentnye-platezhi-06-29"),
        ]
    ])
    
    await send_or_edit_message_with_photo(callback, about_text, reply_markup=about_kb, edit_message=True, image_name="about.png")

@router.callback_query(F.data == "payment_help")
async def payment_help(callback: CallbackQuery):
    help_text = """
📋 <b>Инструкция по оплате</b>

💳 <b>Как правильно оплатить:</b>

1️⃣ <b>Переведите точную сумму</b>
   • Сумма должна совпадать до копейки
   • Проверьте получателя

2️⃣ <b>Сохраните чек</b>
   • Скачайте PDF-чек из банковского приложения
   • Или попросите банк отправить на email

3️⃣ <b>Отправьте PDF боту</b>
   • НЕ скриншот экрана
   • НЕ фотографию чека
   • А именно PDF-документ

4️⃣ <b>Дождитесь подтверждения</b>
   • Администратор проверит чек
   • При подтверждении получите ключ

⚠️ <b>Важно:</b>
• При проблемах обращайтесь в поддержку
"""
    
    help_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, help_text, reply_markup=help_kb, edit_message=True)

@router.callback_query(F.data == "faq")
async def faq(callback: CallbackQuery):
    faq_text = """
❓ <b>Часто задаваемые вопросы</b>

<b>Q: Как долго обрабатывается платеж?</b>
A: Обычно в течение 15-30 минут после отправки чека.

<b>Q: Что делать, если платеж отклонен?</b>
A: Проверьте сумму и получателя, свяжитесь с поддержкой.

<b>Q: Можно ли вернуть деньги?</b>
A: При технических проблемах - да, обращайтесь в поддержку.

<b>Q: Как активировать ключ?</b>
A: Скопируйте ключ и вставьте в соответствующее поле.

<b>Q: Сколько активаций у ключа?</b>
A: У каждого ключа 1 активация.

<b>Q: Что если ключи закончились?</b>
A: Мы пополним склад в течение 1-2 часов.
"""
    
    faq_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Задать вопрос", callback_data="support")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, faq_text, reply_markup=faq_kb, edit_message=True)

@router.callback_query(F.data.startswith("pay_card_"))
async def pay_with_card(callback: CallbackQuery):
    """Обработка оплаты картой"""
    user_id = callback.from_user.id

    # Извлекаем тип подписки
    subscription_type = callback.data.replace("pay_card_", "")

    if subscription_type not in ['week', 'trial', 'month', '3months', '6months', 'year']:
        await callback.answer("❌ Неверный тип подписки", show_alert=True)
        return

    # Получаем цену из базы данных
    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get('prices', {})
                    amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                else:
                    amount = PRICES.get(subscription_type, 0)
    except Exception as e:
        logging.error(f"Ошибка получения цены: {e}")
        amount = PRICES.get(subscription_type, 0)

    # Показываем подтверждение с возможностью ввода промокода
    await _show_pending_payment_message(callback, user_id, subscription_type, 'night', amount, "catalog_night_vpn", payment_method="CARD")


async def _show_pending_payment_message(callback: CallbackQuery, user_id: int, subscription_type: str, vpn_type: str, amount: int, back_catalog: str, payment_method: str = "SBP"):
    """Показать подтверждение оплаты с промокодом"""
    pending_payments[user_id] = {
        'subscription_type': subscription_type,
        'vpn_type': vpn_type,
        'amount': amount,
        'original_amount': amount,
        'promo_code_id': None,
        'promo_code_str': None,
        'discount_percent': 0,
        'back_catalog': back_catalog,
        'payment_method': payment_method,
    }

    vpn_labels = {'night': '🛡️ ОБХОД глушилок + VPN', 'regular': 'Обычный VPN', 'fast': '🚀 Обычный VPN'}
    vpn_label = vpn_labels.get(vpn_type, 'VPN')
    sub_names = {'week': '1 неделя', 'month': '1 месяц', '3months': '3 месяца', '6months': '6 месяцев', 'year': '1 год', '2years': '2 года', 'day': '1 день'}
    sub_name = sub_names.get(subscription_type, 'Подписка')

    method_labels = {'SBP': 'СБП', 'CRYPTO': 'криптовалюта', 'CRYPTOBOT': 'CryptoBot', 'CARD': 'карта', 'REFERRAL': 'реферальный баланс'}
    method_label = method_labels.get(payment_method, payment_method)

    text = f"""💸 <b>Оплата подписки</b>

{vpn_label}
💰 <b>Сумма:</b> {amount} ₽
📅 <b>Тип:</b> {sub_name}
💳 <b>Способ:</b> {method_label}
"""
    promo_callback = f"promo_apply"
    pay_callback = f"pay_confirm"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 У меня есть промокод", callback_data=promo_callback)],
        [InlineKeyboardButton(text=f"💳 Оплатить {amount} ₽", callback_data=pay_callback)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_catalog)]
    ])

    await send_or_edit_message_with_photo(callback, text, reply_markup=kb, edit_message=True if callback.message.text else False)


async def _process_payment_confirmation(callback: CallbackQuery, user_id: int):
    """Создать платеж после подтверждения"""
    data = pending_payments.get(user_id)
    if not data:
        await callback.answer("❌ Сессия истекла, выберите подписку заново", show_alert=True)
        return

    subscription_type = data['subscription_type']
    vpn_type = data['vpn_type']
    amount = data['amount']
    original_amount = data['original_amount']
    promo_code_id = data.get('promo_code_id')
    promo_code_str = data.get('promo_code_str')
    discount_percent = data.get('discount_percent', 0)
    back_catalog = data['back_catalog']
    payment_method = data['payment_method']

    if vpn_type == 'regular':
        full_sub_type = f'regular_{subscription_type}'
    else:
        full_sub_type = subscription_type

    vpn_labels = {'night': '🛡️ ОБХОД глушилок + VPN', 'regular': 'Обычный VPN', 'fast': '🚀 Обычный VPN'}
    vpn_label = vpn_labels.get(vpn_type, 'VPN')
    sub_names = {'week': '1 неделя', 'month': '1 месяц', '3months': '3 месяца', '6months': '6 месяцев', 'year': '1 год', '2years': '2 года', 'day': '1 день'}
    sub_name = sub_names.get(subscription_type, 'Подписка')

    promo_line = ""
    if promo_code_str:
        promo_line = f"\n🎫 <b>Промокод:</b> {promo_code_str} (-{discount_percent}%)\n💰 <b>Было:</b> {original_amount} ₽ → <b>{amount} ₽</b>\n"

    payment_data = None
    error_text = None

    try:
        if payment_method == 'SBP':
            from bot_integration import create_antilopay_payment as create_func
            payment_data = await create_func(
                user_id=user_id, subscription_type=full_sub_type,
                amount=amount, return_url=None, vpn_type=vpn_type
            )
            method_title = "по СБП"
        elif payment_method == 'CARD':
            from bot_integration import create_yookassa_payment as create_func
            payment_data = await create_func(
                user_id=user_id, subscription_type=full_sub_type,
                amount=amount, return_url=None
            )
            method_title = "картой"
        elif payment_method == 'CRYPTO':
            from bot_integration import create_platega_payment as create_func
            payment_data = await create_func(
                user_id=user_id, subscription_type=full_sub_type,
                amount=amount, return_url=None, payment_method=13, vpn_type=vpn_type
            )
            method_title = "криптовалютой"
        elif payment_method == 'CRYPTOBOT':
            from bot_integration import create_cryptobot_payment as create_func
            payment_data = await create_func(
                user_id=user_id, subscription_type=full_sub_type,
                amount=amount, asset='USDT', vpn_type=vpn_type
            )
            method_title = "через CryptoBot"
        elif payment_method == 'REFERRAL':
            from bot_integration import create_referral_payment as create_func
            payment_data = await create_func(
                user_id=user_id, subscription_type=full_sub_type,
                amount=amount, return_url=None, vpn_type=vpn_type
            )
            method_title = "реферальным балансом"
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            return

        if payment_data and 'error' not in payment_data:
            from asgiref.sync import sync_to_async
            from bot_management.models import PromoCode, PromoCodeUsage, TelegramUser, Payment as PaymentModel
            promo_obj = None
            if promo_code_id:
                try:
                    promo_obj = await sync_to_async(PromoCode.objects.get)(id=promo_code_id)
                except PromoCode.DoesNotExist:
                    promo_obj = None
                if promo_obj:
                    if promo_obj.max_uses > 0 and promo_obj.current_uses >= promo_obj.max_uses:
                        await callback.answer("❌ Промокод больше не действует (достигнут лимит)", show_alert=True)
                        return
                    user_usage = await sync_to_async(PromoCodeUsage.objects.filter(promo_code=promo_obj, user_id=user_id).count)()
                    max_per_user = getattr(promo_obj, 'max_uses_per_user', 1)
                    if max_per_user > 0 and user_usage >= max_per_user:
                        await callback.answer("❌ Вы уже использовали этот промокод", show_alert=True)
                        return
            exists = await sync_to_async(PaymentModel.objects.filter(payment_id=payment_data['payment_id']).exists)()
            if exists:
                p = await sync_to_async(PaymentModel.objects.get)(payment_id=payment_data['payment_id'])
                p.promo_code = promo_obj
                p.original_amount = original_amount
                await sync_to_async(p.save)()
                if promo_obj:
                    user_obj, _ = await sync_to_async(TelegramUser.objects.get_or_create)(user_id=user_id)
                    usage, created = await sync_to_async(PromoCodeUsage.objects.get_or_create)(promo_code=promo_obj, user=user_obj, defaults={'payment': p})
                    if not created:
                        logging.warning(f"Повторное использование промокода {promo_obj.code} пользователем {user_id}")
                        await callback.answer("❌ Вы уже использовали этот промокод", show_alert=True)
                        return
                    promo_obj.current_uses += 1
                    await sync_to_async(promo_obj.save)(update_fields=['current_uses'])

            conf_url = payment_data.get('confirmation_url', '')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить", url=conf_url)],
                [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_data['payment_id']}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_catalog)]
            ])

            confirm_text = f"""
💸 <b>Оплата подписки {method_title}</b>

{vpn_label}{promo_line}
📅 <b>Тип:</b> {sub_name}
🆔 <b>ID платежа:</b> {payment_data['payment_id']}

🔗 <b>Нажмите кнопку "Оплатить" для перехода к оплате</b>

<i>После оплаты нажмите "Проверить статус" чтобы получить ключ</i>

Если платеж не проходит напишите в тех поддержку @yamalube61
"""
            await send_or_edit_message_with_photo(callback, confirm_text, reply_markup=keyboard, edit_message=True)
        else:
            error_text = payment_data.get('error', 'Временная недоступность платежной системы') if payment_data else 'Временная недоступность платежной системы'
    except Exception as e:
        logging.error(f"Ошибка создания платежа ({payment_method}): {e}")
        error_text = f"Ошибка: {str(e)}"

    if error_text:
        await send_or_edit_message_with_photo(callback, f"""
❌ <b>Ошибка создания платежа</b>

🔧 <b>Детали:</b>
<code>{error_text}</code>

⏰ Попробуйте позже или обратитесь в поддержку @yamalube61
""", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="pay_confirm")],
            [InlineKeyboardButton(text="⬅️ Каталог", callback_data=back_catalog)]
        ]), edit_message=True)

    if user_id in pending_payments:
        del pending_payments[user_id]


@router.message(PromoState.waiting_code)
async def promo_code_input_handler(message: Message, state: FSMContext):
    """Обработка ввода промокода"""
    user_id = message.from_user.id
    if user_id not in pending_payments:
        await message.answer("❌ Сессия истекла, выберите подписку заново")
        await state.clear()
        return

    code = message.text.strip().upper()
    data = pending_payments[user_id]

    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/promo/validate/'
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={'code': code, 'user_id': user_id}) as resp:
                result = await resp.json()
                if result.get('success'):
                    discount = result['discount_percent']
                    promo_code_id = result['promo_code_id']
                    original_amount = data['amount']
                    discounted = int(original_amount * (100 - discount) / 100)

                    data['amount'] = discounted
                    data['original_amount'] = original_amount
                    data['promo_code_id'] = promo_code_id
                    data['promo_code_str'] = code
                    data['discount_percent'] = discount

                    vpn_labels = {'night': '🛡️ ОБХОД глушилок + VPN', 'regular': 'Обычный VPN', 'fast': '🚀 Обычный VPN'}
                    vpn_label = vpn_labels.get(data['vpn_type'], 'VPN')
                    sub_names = {'week': '1 неделя', 'month': '1 месяц', '3months': '3 месяца', '6months': '6 месяцев', 'year': '1 год', '2years': '2 года', 'day': '1 день'}
                    sub_name = sub_names.get(data['subscription_type'], 'Подписка')

                    method_labels = {'SBP': 'СБП', 'CRYPTO': 'криптовалюта', 'CRYPTOBOT': 'CryptoBot', 'CARD': 'карта', 'REFERRAL': 'реферальный баланс'}
                    method_label = method_labels.get(data['payment_method'], data['payment_method'])

                    text = f"""💸 <b>Оплата подписки</b>

{vpn_label}
📅 <b>Тип:</b> {sub_name}
💳 <b>Способ:</b> {method_label}

🎫 <b>Промокод:</b> {code} (-{discount}%)
💰 <b>Было:</b> {original_amount} ₽
💰 <b>Стало:</b> {discounted} ₽ ✅
"""
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"💳 Оплатить {discounted} ₽", callback_data="pay_confirm")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data=data['back_catalog'])]
                    ])

                    await send_or_edit_message_with_photo(message, text, reply_markup=kb)
                else:
                    error_msg = result.get('message', 'Промокод недействителен')
                    await message.answer(f"❌ {error_msg}")
    except Exception as e:
        logging.error(f"Ошибка проверки промокода: {e}")
        await message.answer("❌ Ошибка проверки промокода. Попробуйте позже.")

    await state.clear()


@router.callback_query(F.data == "promo_apply")
async def promo_apply_handler(callback: CallbackQuery, state: FSMContext):
    """Запрос промокода"""
    user_id = callback.from_user.id
    if user_id not in pending_payments:
        await callback.answer("❌ Сначала выберите подписку", show_alert=True)
        return
    pending_promo_users.add(user_id)
    await state.set_state(PromoState.waiting_code)
    await callback.message.answer("🎫 Введите промокод:")
    await callback.answer()



@router.callback_query(F.data == "pay_confirm")
async def pay_confirm_handler(callback: CallbackQuery):
    """Подтверждение оплаты (создание платежа)"""
    user_id = callback.from_user.id
    await _process_payment_confirmation(callback, user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("pay_sbp_"))
async def pay_with_sbp(callback: CallbackQuery):
    """Обработка оплаты по СБП"""
    user_id = callback.from_user.id

    parts = callback.data.replace("pay_sbp_", "").split("_")
    
    if len(parts) >= 2 and parts[-1] in ['night', 'regular', 'fast']:
        vpn_type = parts.pop()
        subscription_type = "_".join(parts)
    else:
        vpn_type = 'night'
        subscription_type = callback.data.replace("pay_sbp_", "")

    # Проверяем тип подписки
    if vpn_type == 'regular':
        valid_types = ['day', 'month', '3months', '6months', 'year', '2years']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
        back_catalog = "catalog_regular_vpn"
    
    valid_types = ['week', 'trial', 'month', '3months', '6months', 'year']
    if subscription_type not in valid_types:
        await callback.answer("❌ Неверный тип подписки", show_alert=True)
        return
    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get('prices', {})
                    amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                else:
                    amount = PRICES.get(subscription_type, 0)
    except Exception as e:
        logging.error(f"Ошибка получения цены: {e}")
        amount = PRICES.get(subscription_type, 0)
    back_catalog = "catalog_night_vpn"

    await _show_pending_payment_message(callback, user_id, subscription_type, vpn_type, amount, back_catalog, payment_method="SBP")

@router.callback_query(F.data.startswith("pay_crypto_"))
async def pay_with_crypto(callback: CallbackQuery):
    """Обработка оплаты криптовалютой"""
    user_id = callback.from_user.id

    # Извлекаем тип подписки и vpn_type
    # Формат: pay_crypto_{sub_type}_{vpn_type} или pay_crypto_{sub_type} (старый формат)
    parts = callback.data.replace("pay_crypto_", "").split("_")
    
    if len(parts) >= 2 and parts[-1] in ['night', 'regular', 'fast']:
        vpn_type = parts.pop()
        subscription_type = "_".join(parts)
    else:
        vpn_type = 'night'
        subscription_type = callback.data.replace("pay_crypto_", "")

    # Проверяем тип подписки в зависимости от VPN
    if vpn_type == 'regular':
        valid_types = ['day', 'month', '3months', '6months', 'year', '2years']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
    elif vpn_type == 'fast':
        valid_types = ['week', 'trial', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        # Получаем цену из базы данных для ОБХОД глушилок + VPN
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get('prices', {})
                        amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                    else:
                        amount = PRICES.get(subscription_type, 0)
        except Exception as e:
            logging.error(f"Ошибка получения цены: {e}")
            amount = PRICES.get(subscription_type, 0)
    else:  # night vpn
        valid_types = ['week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = FAST_VPN_PRICES.get(subscription_type, 0)

    # Определяем back_catalog
    back_catalog = "catalog_regular_vpn" if vpn_type == 'regular' else ("catalog_fast_vpn" if vpn_type == 'fast' else "catalog_night_vpn")

    # Показываем подтверждение с возможностью ввода промокода
    await _show_pending_payment_message(callback, user_id, subscription_type, vpn_type, amount, back_catalog, payment_method="CRYPTO")


@router.callback_query(F.data.startswith("pay_cryptobot_"))
async def pay_with_cryptobot(callback: CallbackQuery):
    """Обработка оплаты через CryptoBot (TON/USDT)"""
    user_id = callback.from_user.id

    # Извлекаем тип подписки и vpn_type
    # Формат: pay_cryptobot_{sub_type}_{vpn_type}
    parts = callback.data.replace("pay_cryptobot_", "").split("_")

    if len(parts) >= 2 and parts[-1] in ['night', 'regular', 'fast']:
        vpn_type = parts.pop()
        subscription_type = "_".join(parts)
    else:
        vpn_type = 'night'
        subscription_type = callback.data.replace("pay_cryptobot_", "")

    # Проверяем тип подписки в зависимости от VPN
    if vpn_type == 'regular':
        valid_types = ['day', 'month', '3months', '6months', 'year', '2years']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
    elif vpn_type == 'fast':
        valid_types = ['week', 'trial', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        # Получаем цену из базы данных для ОБХОД глушилок + VPN
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get('prices', {})
                        amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                    else:
                        amount = PRICES.get(subscription_type, 0)
        except Exception as e:
            logging.error(f"Ошибка получения цены: {e}")
            amount = PRICES.get(subscription_type, 0)
    else:  # night vpn
        valid_types = ['week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = FAST_VPN_PRICES.get(subscription_type, 0)

    # Определяем back_catalog
    back_catalog = "catalog_regular_vpn" if vpn_type == 'regular' else ("catalog_fast_vpn" if vpn_type == 'fast' else "catalog_night_vpn")

    # Показываем подтверждение с возможностью ввода промокода
    await _show_pending_payment_message(callback, user_id, subscription_type, vpn_type, amount, back_catalog, payment_method="CRYPTOBOT")


@router.callback_query(F.data.startswith("pay_referral_"))
async def pay_with_referral_balance(callback: CallbackQuery):
    """Обработка оплаты реферальными средствами"""
    user_id = callback.from_user.id

    # Извлекаем тип подписки и vpn_type
    parts = callback.data.replace("pay_referral_", "").split("_")
    
    if len(parts) >= 2 and parts[-1] in ['night', 'regular', 'fast']:
        vpn_type = parts.pop()
        subscription_type = "_".join(parts)
    else:
        vpn_type = 'night'
        subscription_type = callback.data.replace("pay_referral_", "")

    # Проверяем тип подписки
    if vpn_type == 'regular':
        valid_types = ['day', 'month', '3months', '6months', 'year', '2years']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
    elif vpn_type == 'fast':
        valid_types = ['week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = FAST_VPN_PRICES.get(subscription_type, 0)
    else:
        valid_types = ['week', 'trial', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get('prices', {})
                        amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                    else:
                        amount = PRICES.get(subscription_type, 0)
        except Exception as e:
            logging.error(f"Ошибка получения цены: {e}")
            amount = PRICES.get(subscription_type, 0)

    # Определяем back_catalog
    back_catalog = "catalog_regular_vpn" if vpn_type == 'regular' else ("catalog_fast_vpn" if vpn_type == 'fast' else "catalog_night_vpn")

    # Показываем подтверждение с возможностью ввода промокода
    await _show_pending_payment_message(callback, user_id, subscription_type, vpn_type, amount, back_catalog, payment_method="REFERRAL")


@router.callback_query(F.data.startswith("pay_bank_card_"))
async def pay_with_bank_card(callback: CallbackQuery):
    """Обработка оплаты банковской картой"""
    user_id = callback.from_user.id

    # Извлекаем тип подписки и vpn_type
    # Формат: pay_bank_card_{sub_type}_{vpn_type} или pay_bank_card_{sub_type} (старый формат)
    parts = callback.data.replace("pay_bank_card_", "").split("_")
    
    if len(parts) >= 2 and parts[-1] in ['night', 'regular', 'fast']:
        vpn_type = parts.pop()
        subscription_type = "_".join(parts)
    else:
        vpn_type = 'night'
        subscription_type = callback.data.replace("pay_bank_card_", "")

    # Проверяем тип подписки в зависимости от VPN
    if vpn_type == 'regular':
        valid_types = ['day', 'month', '3months', '6months', 'year', '2years']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
    elif vpn_type == 'fast':
        valid_types = ['week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        amount = FAST_VPN_PRICES.get(subscription_type, 0)
    else:
        valid_types = ['week', 'trial', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return
        # Получаем цену из базы данных для ОБХОД глушилок + VPN
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = data.get('prices', {})
                        amount = prices.get(subscription_type, PRICES.get(subscription_type, 0))
                    else:
                        amount = PRICES.get(subscription_type, 0)
        except Exception as e:
            logging.error(f"Ошибка получения цены: {e}")
            amount = PRICES.get(subscription_type, 0)

    # Определяем название подписки
    sub_names = {
        'week': '1 неделя',
        'month': '1 месяц',
        '3months': '3 месяца',
        '6months': '6 месяцев',
        'year': '1 год',
        '2years': '2 года'
    }
    sub_name = sub_names.get(subscription_type, 'Подписка')
    vpn_label = "Обычный VPN" if vpn_type == 'regular' else ("🚀 Обычный VPN" if vpn_type == 'fast' else "🛡️ ОБХОД глушилок + VPN")

    # Создаем платеж
    if DJANGO_INTEGRATION:
        if vpn_type == 'regular':
            payment_data = await create_platega_payment(
                user_id=user_id,
                subscription_type=f'regular_{subscription_type}',
                amount=amount,
                return_url=None,
                payment_method=11,
                vpn_type='regular'
            )
            back_catalog = "catalog_regular_vpn"
        elif vpn_type == 'fast':
            payment_data = await create_platega_payment(
                user_id=user_id,
                subscription_type=f'fast_{subscription_type}',
                amount=amount,
                return_url=None,
                payment_method=11,
                vpn_type='fast'
            )
            back_catalog = "catalog_fast_vpn"
        else:
            payment_data = await create_platega_payment(
                user_id=user_id,
                subscription_type=subscription_type,
                amount=amount,
                return_url=None,
                payment_method=11
            )
            back_catalog = "catalog_night_vpn"

        if payment_data:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить картой", url=payment_data['confirmation_url'])],
                [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_data['payment_id']}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_catalog)]
            ])

            await callback.message.answer(f"""
💳 <b>Оплата подписки банковской картой</b>

{vpn_label}
💰 <b>Сумма:</b> {amount} ₽
📅 <b>Тип:</b> {sub_name}
🆔 <b>ID платежа:</b> {payment_data['payment_id']}

🔗 <b>Нажмите кнопку "Оплатить картой" для перехода к оплате</b>

<i>После оплаты ключ будет выдан автоматически</i>

Если платеж не проходит напишите в тех поддержку @yamalube61
""", parse_mode="HTML", reply_markup=keyboard)
        else:
            await callback.message.answer("""
❌ <b>Ошибка создания платежа</b>

🔧 <b>Что происходит:</b>
• Временная недоступность платежной системы
• Попробуйте позже

⏰ <b>Попробуйте через несколько минут</b>

<i>Мы работаем над решением проблемы! 🚀</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=callback.data)],
    [InlineKeyboardButton(text="⬅️ Каталог", callback_data=back_catalog)]
]))
    else:
        await callback.answer("❌ Сервис оплаты недоступен", show_alert=True)

    await callback.answer()

@router.callback_query(F.data == "catalog")
async def show_catalog(callback: CallbackQuery):
    """Показать каталог подписок с выбором типа VPN"""
    catalog_text = """
🔑 <b>Выберите тип VPN</b>

💎 <b>Доступные варианты:</b>

🛡️ <b>ОБХОД глушилок + VPN</b>
• Наш флагманский продукт
• Работает даже при сильных ограничениях
• Стабильное соединение
• Поддержка 24/7

⚡ <b>Обычный VPN</b>
• Недорогой быстрый VPN для повседневных задач
• Поддержка 24/7
• Не обходит белые списки

🚀 <b>Обычный VPN</b>
• Недорогой быстрый VPN для повседневных задач
• Поддержка 24/7

<i>Выберите подходящий вам вариант ⬇️</i>
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ ОБХОД глушилок + VPN", callback_data="catalog_night_vpn")],
        [InlineKeyboardButton(text="🚀 Обычный VPN (от 99₽)", callback_data="catalog_fast_vpn")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])

    await send_or_edit_message_with_photo(callback, catalog_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "catalog_night_vpn")
async def show_night_vpn_catalog(callback: CallbackQuery):
    """Показать каталог ОБХОД глушилок + VPN"""
    # Получаем цены из базы данных
    prices = {}
    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get('prices', {})
    except Exception as e:
        logging.error(f"Ошибка получения цен для каталога: {e}")
        # Fallback на config.py
        prices = {
            'week': PRICES.get('week', 99),
            'month': PRICES.get('month', 390),
            '3months': PRICES.get('3months', 990),
            'year': PRICES.get('year', 3900)
        }

    # Если цены не получены, используем значения по умолчанию
    week_price = prices.get('week', PRICES.get('week', 99))
    month_price = prices.get('month', PRICES.get('month', 390))
    three_months_price = prices.get('3months', PRICES.get('3months', 990))
    six_months_price = prices.get('6months', PRICES.get('6months', 1890))
    year_price = prices.get('year', PRICES.get('year', 3900))

    # Вычисляем экономию
    three_months_savings = (month_price * 3) - three_months_price
    six_months_savings = (month_price * 6) - six_months_price
    year_savings = (month_price * 12) - year_price

    catalog_text = f"""
🛡️ <b>ОБХОД глушилок + VPN - Купить ключ</b>

💎 <b>Выберите подходящую подписку:</b>

📅 <b>1 неделя</b>
• Стоимость: {week_price} ₽
• Срок действия: 7 дней

📅 <b>1 месяц</b>
• Стоимость: {month_price} ₽
• Срок действия: 30 дней

📅 <b>3 месяца</b>
• Стоимость: {three_months_price} ₽
• Срок действия: 90 дней
{f'• 💰 <b>Экономия: {three_months_savings} ₽</b>' if three_months_savings > 0 else ''}

📅 <b>6 месяцев</b>
• Стоимость: {six_months_price} ₽
• Срок действия: 180 дней
{f'• 💰 <b>Экономия: {six_months_savings} ₽</b>' if six_months_savings > 0 else ''}

📅 <b>12 месяцев (год)</b>
• Стоимость: {year_price} ₽
• Срок действия: 365 дней
{f'• 💰 <b>Экономия: {year_savings} ₽</b>' if year_savings > 0 else ''}

🔥 <b>Работает на всех операторах.</b>
✅ <b>Работает во всех городах.</b>

<i>Выберите подписку и оплатите ⬇️</i>
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 1 неделя - {week_price} ₽", callback_data="sub_night_week")],
        [InlineKeyboardButton(text=f"📅 1 месяц - {month_price} ₽", callback_data="sub_month")],
        [InlineKeyboardButton(text=f"📅 3 месяца - {three_months_price} ₽", callback_data="sub_3months")],
        [InlineKeyboardButton(text=f"📅 6 месяцев - {six_months_price} ₽", callback_data="sub_6months")],
        [InlineKeyboardButton(text=f"📅 12 месяцев - {year_price} ₽", callback_data="sub_year")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])

    await send_or_edit_message_with_photo(callback, catalog_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "catalog_regular_vpn")
async def show_regular_vpn_catalog(callback: CallbackQuery):
    """Показать каталог Обычный VPN"""
    # Цены из config.py
    day_price = REGULAR_VPN_PRICES.get('day', 19)
    month_price = REGULAR_VPN_PRICES.get('month', 190)
    three_months_price = REGULAR_VPN_PRICES.get('3months', 509)
    six_months_price = REGULAR_VPN_PRICES.get('6months', 950)
    year_price = REGULAR_VPN_PRICES.get('year', 1760)
    two_years_price = REGULAR_VPN_PRICES.get('2years', 3150)

    # Вычисляем экономию
    month_savings = (day_price * 30) - month_price
    three_months_savings = (month_price * 3) - three_months_price
    six_months_savings = (month_price * 6) - six_months_price
    year_savings = (month_price * 12) - year_price
    two_years_savings = (year_price * 2) - two_years_price

    catalog_text = f"""
⚡ <b>Обычный VPN - Купить ключ</b>

💎 <b>Выберите подходящую подписку:</b>

• Недорогой быстрый VPN для повседневных задач
• Не обходит белые списки

⚡ <b>1 день</b>
• Стоимость: {day_price} ₽
• Срок действия: 24 часа

📅 <b>1 месяц</b>
• Стоимость: {month_price} ₽
• Срок действия: 30 дней
{f'• 💰 <b>Экономия: {month_savings} ₽</b>' if month_savings > 0 else ''}

📅 <b>3 месяца</b>
• Стоимость: {three_months_price} ₽
• Срок действия: 90 дней
{f'• 💰 <b>Экономия: {three_months_savings} ₽</b>' if three_months_savings > 0 else ''}

📅 <b>6 месяцев</b>
• Стоимость: {six_months_price} ₽
• Срок действия: 180 дней
{f'• 💰 <b>Экономия: {six_months_savings} ₽</b>' if six_months_savings > 0 else ''}

📅 <b>1 год</b>
• Стоимость: {year_price} ₽
• Срок действия: 365 дней
{f'• 💰 <b>Экономия: {year_savings} ₽</b>' if year_savings > 0 else ''}

📅 <b>2 года</b>
• Стоимость: {two_years_price} ₽
• Срок действия: 730 дней
{f'• 💰 <b>Экономия: {two_years_savings} ₽</b>' if two_years_savings > 0 else ''}

✅ <b>Автоматическая генерация ключей</b>
✅ <b>Поддержка 24/7</b>

<i>Выберите подписку и оплатите ⬇️</i>
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ 1 день - {day_price} ₽", callback_data="sub_regular_day")],
        [InlineKeyboardButton(text=f"📅 1 месяц - {month_price} ₽", callback_data="sub_regular_month")],
        [InlineKeyboardButton(text=f"📅 3 месяца - {three_months_price} ₽", callback_data="sub_regular_3months")],
        [InlineKeyboardButton(text=f"📅 6 месяцев - {six_months_price} ₽", callback_data="sub_regular_6months")],
        [InlineKeyboardButton(text=f"📅 1 год - {year_price} ₽", callback_data="sub_regular_year")],
        [InlineKeyboardButton(text=f"📅 2 года - {two_years_price} ₽", callback_data="sub_regular_2years")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])

    await send_or_edit_message_with_photo(callback, catalog_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "catalog_fast_vpn")
async def show_fast_vpn_catalog(callback: CallbackQuery):
    """Показать каталог Обычный VPN"""
    week_price = FAST_VPN_PRICES.get('week', 99)
    month_price = FAST_VPN_PRICES.get('month', 150)
    three_months_price = FAST_VPN_PRICES.get('3months', 399)
    six_months_price = FAST_VPN_PRICES.get('6months', 749)
    year_price = FAST_VPN_PRICES.get('year', 1390)

    three_months_savings = (month_price * 3) - three_months_price
    six_months_savings = (month_price * 6) - six_months_price
    year_savings = (month_price * 12) - year_price

    catalog_text = f"""
🚀 <b>Обычный VPN - Купить ключ</b>

💎 <b>Выберите подходящую подписку:</b>

• Недорогой быстрый VPN для повседневных задач

📅 <b>1 день</b>
• Стоимость: {day_price} ₽
• Срок действия: 24 часа

📅 <b>1 месяц</b>
• Стоимость: {month_price} ₽
• Срок действия: 30 дней
{f'• 💰 <b>Экономия: {month_savings} ₽</b>' if month_savings > 0 else ''}

📅 <b>3 месяца</b>
• Стоимость: {three_months_price} ₽
• Срок действия: 90 дней
{f'• 💰 <b>Экономия: {three_months_savings} ₽</b>' if three_months_savings > 0 else ''}

📅 <b>6 месяцев</b>
• Стоимость: {six_months_price} ₽
• Срок действия: 180 дней
{f'• 💰 <b>Экономия: {six_months_savings} ₽</b>' if six_months_savings > 0 else ''}

📅 <b>1 год</b>
• Стоимость: {year_price} ₽
• Срок действия: 365 дней
{f'• 💰 <b>Экономия: {year_savings} ₽</b>' if year_savings > 0 else ''}

📅 <b>2 года</b>
• Стоимость: {two_years_price} ₽
• Срок действия: 730 дней
{f'• 💰 <b>Экономия: {two_years_savings} ₽</b>' if two_years_savings > 0 else ''}

✅ <b>Автоматическая генерация ключей</b>
✅ <b>Поддержка 24/7</b>

<i>Выберите подписку и оплатите ⬇️</i>
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 1 день - {day_price} ₽", callback_data="sub_regular_day")],
        [InlineKeyboardButton(text=f"📅 1 месяц - {month_price} ₽", callback_data="sub_regular_month")],
        [InlineKeyboardButton(text=f"📅 3 месяца - {three_months_price} ₽", callback_data="sub_regular_3months")],
        [InlineKeyboardButton(text=f"📅 6 месяцев - {six_months_price} ₽", callback_data="sub_regular_6months")],
        [InlineKeyboardButton(text=f"📅 1 год - {year_price} ₽", callback_data="sub_regular_year")],
        [InlineKeyboardButton(text=f"📅 2 года - {two_years_price} ₽", callback_data="sub_regular_2years")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])

    await send_or_edit_message_with_photo(callback, catalog_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "get_key_menu")
async def show_get_key_menu(callback: CallbackQuery):
    """Показать меню получения ключа (пробный или платный)"""
    user_id = callback.from_user.id

    # СРАЗУ отвечаем на callback чтобы убрать "часики"
    await callback.answer()

    menu_text = """
🔑 <b>Получить VPN ключ</b>

Выберите тип VPN:
"""

    # Проверяем статус пробных ключей ПАРАЛЛЕЛЬНО (в 3 раза быстрее!)
    night_trial_used = False
    regular_trial_used = False
    fast_trial_used = False
    
    if DJANGO_INTEGRATION:
        async def check_trial(vpn_type):
            try:
                import aiohttp
                api_url = f'{DJANGO_API_URL}/bot_management/api/user/{user_id}/trial_status/?vpn_type={vpn_type}'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data.get('trial_used', False)
            except:
                pass
            return False
        
        night_trial_used, regular_trial_used, fast_trial_used = await asyncio.gather(
            check_trial('night'),
            check_trial('regular'),
            check_trial('fast'),
            return_exceptions=True
        )
        # Если gather вернул exception вместо значения, считаем False
        night_trial_used = night_trial_used if isinstance(night_trial_used, bool) else False
        regular_trial_used = regular_trial_used if isinstance(regular_trial_used, bool) else False
        fast_trial_used = fast_trial_used if isinstance(fast_trial_used, bool) else False

    # Формируем клавиатуру в зависимости от статуса пробных ключей
    keyboard = []
    
    if not night_trial_used:
        keyboard.append([InlineKeyboardButton(text="🌙Пробный ОБХОД глушилок + VPN", callback_data="trial_key_info_night")])
    
    if not fast_trial_used:
        keyboard.append([InlineKeyboardButton(text="🚀Пробный Обычный VPN", callback_data="trial_key_info_fast")])
    
    # Если все пробные ключи использованы, показываем сообщение
    if night_trial_used and regular_trial_used and fast_trial_used:
        menu_text = """
🔑 <b>Получить VPN ключ</b>

❌ <b>Пробные ключи уже использованы</b>

Вы уже использовали все пробные подписки.
💳 Приобретите полную подписку для продолжения использования.
"""

    keyboard.append([InlineKeyboardButton(text="💳 Купить подписку", callback_data="catalog")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await send_or_edit_message_with_photo(callback, menu_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "trial_key_info_night")
async def show_trial_key_info_night(callback: CallbackQuery):
    """Показать информацию о пробном ключе ОБХОД глушилок + VPN"""
    user_id = callback.from_user.id
    
    # Проверяем, использовал ли пользователь пробный ключ ОБХОД глушилок + VPN
    trial_used = False
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/user/{user_id}/trial_status/?vpn_type=night'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        trial_used = data.get('trial_used', False)
        except:
            pass
    
    if trial_used:
        info_text = """
❌ <b>Пробный ключ ОБХОД глушилок + VPN уже использован</b>

Вы уже использовали пробную подписку для ОБХОД глушилок + VPN.

💳 <b>Доступные варианты:</b>
• Приобрести полную подписку
• Попробовать Обычный VPN или 🚀 Обычный VPN (доступны пробные ключи)
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку ОБХОД глушилок + VPN", callback_data="catalog_night_vpn")],
            [InlineKeyboardButton(text="⚡ Попробовать Обычный VPN", callback_data="trial_key_info_regular")],
            [InlineKeyboardButton(text="🚀 Попробовать Обычный VPN", callback_data="trial_key_info_fast")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])
    else:
        info_text = """
🎁 <b>Пробный ключ ОБХОД глушилок + VPN</b>

⚠️ <b>Внимание:</b> Ключ предоставляется только на 3 дня и выдается единожды.

📅 <b>Условия:</b>
• Длительность: 3 дня
• Количество активаций: 1
• 💳 <b>Требуется привязка карты</b> (через СБП)

<i>После привязки ключ придёт автоматически!</i>
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Получить пробную подписку ОБХОД глушилок + VPN", callback_data="get_trial_key_night")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])

    await send_or_edit_message_with_photo(callback, info_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "trial_key_info_regular")
async def show_trial_key_info_regular(callback: CallbackQuery):
    """Показать информацию о пробном ключе Обычный VPN"""
    user_id = callback.from_user.id
    
    # Проверяем, использовал ли пользователь пробный ключ Обычный VPN
    trial_used = False
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/user/{user_id}/trial_status/?vpn_type=regular'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        trial_used = data.get('trial_used', False)
        except:
            pass
    
    if trial_used:
        info_text = """
❌ <b>Пробный ключ Обычный VPN уже использован</b>

Вы уже использовали пробную подписку для Обычный VPN.

💳 <b>Доступные варианты:</b>
• Приобрести полную подписку
• Попробовать ОБХОД глушилок + VPN или 🚀 Обычный VPN (доступны пробные ключи)
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку Обычный VPN", callback_data="catalog_regular_vpn")],
            [InlineKeyboardButton(text="🌙 Попробовать ОБХОД глушилок + VPN", callback_data="trial_key_info_night")],
            [InlineKeyboardButton(text="🚀 Попробовать Обычный VPN", callback_data="trial_key_info_fast")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])
    else:
        info_text = """
🎁 <b>Пробный ключ Обычный VPN</b>

⚠️ <b>Внимание:</b> Ключ предоставляется только на 1 день и выдается единожды.

📅 <b>Условия:</b>
• Длительность: 1 день
• Количество активаций: 1
• 💳 <b>Требуется привязка карты</b> (через СБП)

<i>После привязки ключ придёт автоматически!</i>
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Получить пробную подписку Обычный VPN", callback_data="get_trial_key_regular")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])

    await send_or_edit_message_with_photo(callback, info_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "trial_offer_get")
async def trial_offer_get_handler(callback: CallbackQuery):
    """Обработчик кнопки получения пробного доступа из приветствия"""
    user_id = callback.from_user.id
    await issue_trial_key_for_vpn_type(callback, user_id, 'night', 'ОБХОД глушилок + VPN')


@router.callback_query(F.data == "get_trial_key_night")
async def get_trial_key_night(callback: CallbackQuery):
    """Выдать пробный ключ ОБХОД глушилок + VPN пользователю"""
    user_id = callback.from_user.id
    await issue_trial_key_for_vpn_type(callback, user_id, 'night', 'ОБХОД глушилок + VPN')


@router.callback_query(F.data == "get_trial_key_regular")
async def get_trial_key_regular(callback: CallbackQuery):
    """Выдать пробный ключ Обычный VPN пользователю"""
    user_id = callback.from_user.id
    await issue_trial_key_for_vpn_type(callback, user_id, 'regular', 'Обычный VPN')


@router.callback_query(F.data == "trial_key_info_fast")
async def show_trial_key_info_fast(callback: CallbackQuery):
    """Показать информацию о пробном ключе Обычный VPN"""
    user_id = callback.from_user.id
    
    trial_used = False
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/user/{user_id}/trial_status/?vpn_type=fast'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        trial_used = data.get('trial_used', False)
        except:
            pass
    
    if trial_used:
        info_text = """
❌ <b>Пробный ключ Обычный VPN уже использован</b>

Вы уже использовали пробную подписку для Обычный VPN.

💳 <b>Доступные варианты:</b>
• Приобрести полную подписку
• Попробовать ОБХОД глушилок + VPN (если доступен)
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку Обычный VPN", callback_data="catalog_fast_vpn")],
            [InlineKeyboardButton(text="🌙 Попробовать ОБХОД глушилок + VPN", callback_data="trial_key_info_night")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])
    else:
        info_text = """
🎁 <b>Пробный ключ Обычный VPN</b>

⚠️ <b>Внимание:</b> Ключ предоставляется только на 1 день и выдается единожды.

📅 <b>Условия:</b>
• Длительность: 1 день
• Количество активаций: 1
• 💳 <b>Требуется привязка карты</b> (через СБП)

<i>После привязки ключ придёт автоматически!</i>
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Получить пробную подписку Обычный VPN", callback_data="get_trial_key_fast")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])

    await send_or_edit_message_with_photo(callback, info_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "get_trial_key_fast")
async def get_trial_key_fast(callback: CallbackQuery):
    """Выдать пробный ключ Обычный VPN пользователю"""
    user_id = callback.from_user.id
    await issue_trial_key_for_vpn_type(callback, user_id, 'fast', '🚀 Обычный VPN')


async def issue_trial_key_for_vpn_type(callback, user_id, vpn_type, vpn_label):
    """Создаёт Antilopay платеж для привязки карты и получения пробного ключа"""
    if not DJANGO_INTEGRATION:
        await send_or_edit_message_with_photo(callback, "❌ Система недоступна. Попробуйте позже.", edit_message=True, image_name="catalog.png")
        return

    import aiohttp
    from config import PRICES, REGULAR_VPN_PRICES, FAST_VPN_PRICES

    # При привязке карты будет выдан ключ на 3 дня, а при первом списании (через 1 день) - продлен на 30 дней
    trial_config = {
        'night': {'sub_type': 'trial', 'price': PRICES.get('month', 390)},
        'regular': {'sub_type': 'regular_trial', 'price': REGULAR_VPN_PRICES.get('month', 190)},
        'fast': {'sub_type': 'fast_trial', 'price': FAST_VPN_PRICES.get('month', 150)},
    }
    cfg = trial_config.get(vpn_type, trial_config['night'])
    amount = cfg['price']
    sub_type = cfg['sub_type']

    await callback.answer()

    # Создаём платеж с delay=1 (первое списание через 1 день)
    from bot_integration import create_antilopay_payment
    payment_data = await create_antilopay_payment(
        user_id=user_id, subscription_type=sub_type,
        amount=amount, vpn_type=vpn_type, delay=1
    )

    if not payment_data or 'error' in payment_data:
        error_msg = payment_data.get('error', 'Ошибка создания платежа') if payment_data else 'Нет ответа от сервера'
        error_text = f"""
❌ <b>Ошибка создания платежа</b>

{error_msg}

Попробуйте позже или обратитесь в поддержку.
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Поддержка", url="https://t.me/yamalube61")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="get_key_menu")]
        ])
        await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
        return

    payment_id = payment_data['payment_id']
    conf_url = payment_data.get('confirmation_url', '')

    text = f"""
🎁 <b>Забери бесплатный пробный период {vpn_label} — 3 ДНЯ 🔥</b>

Но нужно пройти верификацию.

<b>Почему мы привязываем счёт и почему это в ваших интересах:</b>

Агенты РКН массово заходят в VPN-боты, получают бесплатный доступ, вычисляют IP серверов и отправляют их на блокировку. Именно так за 3 месяца заблокировали 1069 сервисов.

Верификация привязкой счёта отсекает агентов – они не могут каждый раз создавать новую карту. Результат: наши серверы стабильны, белые списки обходятся без перебоев.

Мы не спишем ничего с этого счёта. После того, как привяжете счёт — вы автоматически получите <b>ПРОБНЫЙ ПЕРИОД — 3 ДНЯ 🔥</b>
"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Привязать карту", url=conf_url)],
        [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_payment_{payment_id}")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"),
         InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/yamalube61")]
    ])

    await send_or_edit_message_with_photo(callback, text, reply_markup=keyboard, edit_message=True, image_name="catalog.png")


# Обработчики покупки с баланса удалены - теперь используется прямая оплата через ЮKassa
# @router.callback_query(F.data == "sub_month_balance")
# async def buy_month_balance(callback: CallbackQuery):
#     """Покупка месячной подписки с баланса"""
#     user_id = callback.from_user.id
#     
#     if DJANGO_INTEGRATION:
#         try:
#             # Получаем профиль пользователя
#             profile_data = await get_user_profile(user_id)
#             
#             if profile_data and profile_data.get('success'):
#                 user_info = profile_data.get('user', {})
#                 balance = float(user_info.get('balance', 0))
#                 price = 1900
#                 
#                 if balance >= price:
#                     # Достаточно средств - покупаем
#                     success_text = f"""
# ✅ <b>Покупка месячной подписки</b>
# 
# 💰 <b>Списано с баланса:</b> {price} ₽
# 💳 <b>Остаток на балансе:</b> {balance - price} ₽
# 
# 🔑 <b>Ваш ключ будет выдан автоматически</b>
# 
# <i>Обрабатываем покупку...</i>
# """
#                     
#                     kb = InlineKeyboardMarkup(inline_keyboard=[
#                         [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
#                     ])
#                     
#                     await send_or_edit_message_with_photo(callback, success_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
#                     
#                     # Покупаем подписку с баланса
#                     purchase_result = await buy_subscription_with_balance(user_id, "month", price)
#                     
#                     if purchase_result and purchase_result.get('success'):
#                         # Успешная покупка
#                         issued_key = purchase_result.get('issued_key')
#                         new_balance = purchase_result.get('new_balance', balance - price)
#                         
#                         # Отправляем подтверждение с ключом
#                         confirmation_text = f"""
# 🎉 <b>Покупка завершена!</b>
# 
# 🔑 <b>Ваш ключ:</b> {issued_key}
# 💳 <b>Остаток на балансе:</b> {new_balance} ₽
# 
# <i>Спасибо за покупку! 🚀</i>
# """
#                         
#                         await send_or_edit_message_with_photo(callback, confirmation_text, edit_message=False, image_name="catalog.png")
#                         
#                         # Отправляем инструкцию отдельным сообщением
#                         instruction_text = f"""
# 🔑 <b>Инструкция по активации ключа</b>
# 
# <b>Ваш ключ:</b> {issued_key}
# 
# Копируем и вставляем ссылку, а не содержимое!!!
# 
# 1. Скачиваешь приложение "v2RayTun"
# 2. Заходишь
# 3. Справа вверху +, нажимаешь
# 4. Добавить из буфера
# 5. Разрешить вставку
# 6. Выбираешь «От глушилок»
# 7. Включаешь
# 8. Готово!
# 
# <i>В случае повторного ввода 🔑 на другом устройстве, если он на это не рассчитан, система его отключает, возврат средств в таком случае не делаем.</i>
# """
#                         await send_or_edit_message_with_photo(callback, instruction_text, edit_message=False, image_name="catalog.png")
#                     else:
#                         # Ошибка покупки
#                         error_text = """
# ❌ <b>Ошибка покупки</b>
# 
# 🔧 <b>Что происходит:</b>
# • Временная недоступность системы
# • Попробуйте позже
# 
# ⏰ <b>Попробуйте через несколько минут</b>
# """
#                         await callback.message.answer(error_text)
#                     
#                 else:
#                     # Недостаточно средств
#                     need_more = price - balance
#                     error_text = f"""
# ❌ <b>Недостаточно средств</b>
# 
# 💰 <b>Ваш баланс:</b> {balance} ₽
# 💳 <b>Нужно:</b> {price} ₽
# 🔴 <b>Не хватает:</b> {need_more} ₽
# 
# 💡 <b>Пополните баланс и попробуйте снова</b>
# """
#                     
#                     kb = InlineKeyboardMarkup(inline_keyboard=[
#                         [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance")],
#                         [InlineKeyboardButton(text="⬅️ Каталог", callback_data="catalog")]
#                     ])
#                     
#                     await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
#             else:
#                 await send_or_edit_message_with_photo(callback, "❌ Ошибка загрузки баланса. Попробуйте позже.", edit_message=True, image_name="catalog.png")
#         except Exception as e:
#             logging.error(f"Ошибка покупки месячной подписки: {e}")
#             await send_or_edit_message_with_photo(callback, "❌ Произошла ошибка. Попробуйте позже.", edit_message=True, image_name="catalog.png")
#     else:
#         await send_or_edit_message_with_photo(callback, "❌ Система недоступна. Попробуйте позже.", edit_message=True, image_name="catalog.png")

# @router.callback_query(F.data == "sub_year_balance")
# async def buy_year_balance(callback: CallbackQuery):
#     """Покупка годовой подписки с баланса"""
#     user_id = callback.from_user.id
#     
#     if DJANGO_INTEGRATION:
#         try:
#             # Получаем профиль пользователя
#             profile_data = await get_user_profile(user_id)
#             
#             if profile_data and profile_data.get('success'):
#                 user_info = profile_data.get('user', {})
#                 balance = float(user_info.get('balance', 0))
#                 price = 6900
#                 
#                 if balance >= price:
#                     # Достаточно средств - покупаем
#                     success_text = f"""
# ✅ <b>Покупка пожизненной подписки</b>
# 
# 💰 <b>Списано с баланса:</b> {price} ₽
# 💳 <b>Остаток на балансе:</b> {balance - price} ₽
# 
# 🔑 <b>Ваш ключ будет выдан автоматически</b>
# 
# <i>Обрабатываем покупку...</i>
# """
#                     
#                     kb = InlineKeyboardMarkup(inline_keyboard=[
#                         [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
#                     ])
#                     
#                     await send_or_edit_message_with_photo(callback, success_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
#                     
#                     # Покупаем подписку с баланса
#                     purchase_result = await buy_subscription_with_balance(user_id, "year", price)
#                     
#                     if purchase_result and purchase_result.get('success'):
#                         # Успешная покупка
#                         issued_key = purchase_result.get('issued_key')
#                         new_balance = purchase_result.get('new_balance', balance - price)
#                         
#                         # Отправляем подтверждение с ключом
#                         confirmation_text = f"""
# 🎉 <b>Покупка завершена!</b>
# 
# 🔑 <b>Ваш ключ:</b> {issued_key}
# 💳 <b>Остаток на балансе:</b> {new_balance} ₽
# 
# <i>Спасибо за покупку! 🚀</i>
# """
#                         
#                         await send_or_edit_message_with_photo(callback, confirmation_text, edit_message=False, image_name="catalog.png")
#                         
#                         # Отправляем инструкцию отдельным сообщением
#                         instruction_text = f"""
# 🔑 <b>Инструкция по активации ключа</b>
# 
# <b>Ваш ключ:</b> {issued_key}
# 
# Копируем и вставляем ссылку, а не содержимое!!!
# 
# 1. Скачиваешь приложение "v2RayTun"
# 2. Заходишь
# 3. Справа вверху +, нажимаешь
# 4. Добавить из буфера
# 5. Разрешить вставку
# 6. Выбираешь «От глушилок»
# 7. Включаешь
# 8. Готово!
# 
# <i>В случае повторного ввода 🔑 на другом устройстве, если он на это не рассчитан, система его отключает, возврат средств в таком случае не делаем.</i>
# """
#                         await send_or_edit_message_with_photo(callback, instruction_text, edit_message=False, image_name="catalog.png")
#                     else:
#                         # Ошибка покупки
#                         error_text = """
# ❌ <b>Ошибка покупки</b>
# 
# 🔧 <b>Что происходит:</b>
# • Временная недоступность системы
# • Попробуйте позже
# 
# ⏰ <b>Попробуйте через несколько минут</b>
# """
#                         await callback.message.answer(error_text)
#                     
#                 else:
#                     # Недостаточно средств
#                     need_more = price - balance
#                     error_text = f"""
# ❌ <b>Недостаточно средств</b>
# 
# 💰 <b>Ваш баланс:</b> {balance} ₽
# 💳 <b>Нужно:</b> {price} ₽
# 🔴 <b>Не хватает:</b> {need_more} ₽
# 
# 💡 <b>Пополните баланс и попробуйте снова</b>
# """
#                     
#                     kb = InlineKeyboardMarkup(inline_keyboard=[
#                         [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance")],
#                         [InlineKeyboardButton(text="⬅️ Каталог", callback_data="catalog")]
#                     ])
#                     
#                     await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
#             else:
#                 await send_or_edit_message_with_photo(callback, "❌ Ошибка загрузки баланса. Попробуйте позже.", edit_message=True, image_name="catalog.png")
#         except Exception as e:
#             logging.error(f"Ошибка покупки пожизненной подписки: {e}")
#             await send_or_edit_message_with_photo(callback, "❌ Произошла ошибка. Попробуйте позже.", edit_message=True, image_name="catalog.png")
#     else:
#         await send_or_edit_message_with_photo(callback, "❌ Система недоступна. Попробуйте позже.", edit_message=True, image_name="catalog.png")

@router.callback_query(F.data == "my_keys")
async def show_my_keys(callback: CallbackQuery):
    """Показать меню выбора типа VPN для просмотра ключей"""
    user_id = callback.from_user.id

    if DJANGO_INTEGRATION:
        try:
            import aiohttp

            # Запрос к API для получения ключей пользователя
            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get('success') and data.get('keys'):
                            hidden = data.get('hidden_expired_count', 0)
                            # Считаем количество ключей каждого типа
                            # Обычный VPN: subscription_type начинается с 'regular'
                            regular_count = sum(1 for key in data['keys'] if key.get('subscription_type', '').startswith('regular'))
                            night_count = len(data['keys']) - regular_count
                            
                            keys_text = "🔑 <b>Ваши ключи подписки</b>\n\n"
                            
                            if regular_count > 0:
                                keys_text += f"🌍 <b>Обычный VPN:</b> {regular_count} ключ(а)\n"
                            if night_count > 0:
                                keys_text += f"🛡️ <b>ОБХОД глушилок + VPN:</b> {night_count} ключ(а)\n"
                            
                            if regular_count == 0 and night_count == 0:
                                keys_text += "\n📭 <b>У вас пока нет ключей</b>"
                            if hidden > 0:
                                keys_text += f"\n\n<i>🗑 {hidden} истекших ключ(а) скрыто</i>"

                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text=f"Обычный VPN ({regular_count})", callback_data="my_keys_regular")],
                                [InlineKeyboardButton(text=f"🛡️ ОБХОД глушилок + VPN ({night_count})", callback_data="my_keys_night")],
                                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys")],
                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                            ])

                            await send_or_edit_message_with_photo(callback, keys_text, reply_markup=kb, edit_message=True, image_name="my_keys.png")
                        else:
                            # Нет ключей
                            no_keys_text = """
🔑 <b>Ваши ключи подписки</b>

📭 <b>У вас пока нет ключей</b>

💡 <b>Как получить ключ:</b>
• Выберите подходящую подписку
• Оплатите через безопасную платежную систему
• Получите ключ автоматически
"""

                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                            ])

                            await send_or_edit_message_with_photo(callback, no_keys_text, reply_markup=kb, edit_message=True, image_name="my_keys.png")
                    else:
                        # Ошибка API
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys")],
                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                        ])
                        await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка API</b>\n\nПопробуйте позже.", reply_markup=kb, edit_message=True, image_name="my_keys.png")
        except Exception as e:
            logging.error(f"Ошибка показа ключей: {e}")
            await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка загрузки ключей</b>\n\nПопробуйте позже.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ]), edit_message=True, image_name="my_keys.png")
    else:
        await send_or_edit_message_with_photo(callback, "❌ <b>Django интеграция недоступна</b>", edit_message=True, image_name="my_keys.png")


@router.callback_query(F.data == "my_keys_regular")
async def show_my_keys_regular(callback: CallbackQuery):
    """Показать ключи Обычного VPN"""
    user_id = callback.from_user.id

    if DJANGO_INTEGRATION:
        try:
            import aiohttp

            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get('success') and data.get('keys'):
                            hidden = data.get('hidden_expired_count', 0)
                            # Фильтруем только Обычный VPN (subscription_type начинается с 'regular')
                            regular_keys = [
                                key for key in data['keys']
                                if key.get('subscription_type', '').startswith('regular')
                            ]

                            if regular_keys:
                                keys_text = "🌍 <b>Обычный VPN - Ваши ключи</b>\n\n"
                                if hidden > 0:
                                    keys_text += f"<i>🗑 {hidden} истекших ключ(а) скрыто</i>\n\n"
                                kb_buttons = []

                                for i, key in enumerate(regular_keys, 1):
                                    # Нормальное название типа подписки
                                    sub_type_raw = key.get('subscription_type', 'Неизвестно')
                                    sub_type_map = {
                                        'regular_day': '1 день',
                                        'regular_month': '1 месяц',
                                        'regular_3months': '3 месяца',
                                        'regular_6months': '6 месяцев',
                                        'regular_year': '1 год',
                                        'regular_2years': '2 года'
                                    }
                                    sub_type = sub_type_map.get(sub_type_raw, sub_type_raw.replace('regular_', ''))
                                    payment_id = key.get('payment_id', i)

                                    from datetime import datetime
                                    try:
                                        purchase_date = datetime.fromisoformat(key['created_at'].replace('Z', '+00:00'))
                                        formatted_date = purchase_date.strftime('%d.%m.%Y')
                                    except:
                                        formatted_date = "Неизвестно"

                                    button_text = f"🌍 {i}. {sub_type} ({formatted_date})"
                                    if len(button_text) > 64:
                                        button_text = button_text[:61] + "..."

                                    kb_buttons.append([InlineKeyboardButton(
                                        text=button_text,
                                        callback_data=f"key_detail:{payment_id}"
                                    )])

                                kb_buttons.append([
                                    InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys"),
                                    InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")
                                ])

                                kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                                await send_or_edit_message_with_photo(callback, keys_text, reply_markup=kb, edit_message=True, image_name="my_keys.png")
                            else:
                                no_keys_text = "📭 <b>У вас нет ключей Обычного VPN</b>"
                                if hidden > 0:
                                    no_keys_text += f"\n\n<i>🗑 {hidden} истекших ключ(а) скрыто из списка</i>"
                                await send_or_edit_message_with_photo(callback, no_keys_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")],
                                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                ]), edit_message=True, image_name="my_keys.png")
                        else:
                            await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка загрузки ключей</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys_regular")],
                                [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
                            ]), edit_message=True, image_name="my_keys.png")
                    else:
                        await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка API</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="my_keys_regular")],
                            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
                        ]), edit_message=True, image_name="my_keys.png")
        except Exception as e:
            logging.error(f"Ошибка показа ключей Обычного VPN: {e}")
            await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка загрузки ключей</b>\n\nПопробуйте позже.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys_regular")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
            ]), edit_message=True, image_name="my_keys.png")
    else:
        await send_or_edit_message_with_photo(callback, "❌ <b>Django интеграция недоступна</b>", edit_message=True, image_name="my_keys.png")


@router.callback_query(F.data == "my_keys_night")
async def show_my_keys_night(callback: CallbackQuery):
    """Показать ключи ОБХОД глушилок + VPN"""
    user_id = callback.from_user.id

    if DJANGO_INTEGRATION:
        try:
            import aiohttp

            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get('success') and data.get('keys'):
                            hidden = data.get('hidden_expired_count', 0)
                            # Фильтруем только ОБХОД глушилок + VPN (subscription_type не начинается с 'regular')
                            night_keys = [
                                key for key in data['keys']
                                if not key.get('subscription_type', '').startswith('regular')
                            ]
                            
                            if night_keys:
                                keys_text = "🛡️ <b>ОБХОД глушилок + VPN - Ваши ключи</b>\n\n"
                                if hidden > 0:
                                    keys_text += f"<i>🗑 {hidden} истекших ключ(а) скрыто</i>\n\n"
                                kb_buttons = []

                                for i, key in enumerate(night_keys, 1):
                                    if key['subscription_type'] == 'trial':
                                        sub_type = "Пробная (3 дня)"
                                    elif key['subscription_type'] == 'month':
                                        sub_type = "Месячная"
                                    elif key['subscription_type'] == '3months':
                                        sub_type = "3 месяца"
                                    elif key['subscription_type'] == '6months':
                                        sub_type = "6 месяцев"
                                    elif key['subscription_type'] == 'year':
                                        sub_type = "Годовая"
                                    else:
                                        sub_type = key['subscription_type']
                                    
                                    payment_id = key.get('payment_id', i)
                                    
                                    from datetime import datetime
                                    try:
                                        purchase_date = datetime.fromisoformat(key['created_at'].replace('Z', '+00:00'))
                                        formatted_date = purchase_date.strftime('%d.%m.%Y')
                                    except:
                                        formatted_date = "Неизвестно"

                                    button_text = f"🌙 {i}. {sub_type} ({formatted_date})"
                                    if len(button_text) > 64:
                                        button_text = button_text[:61] + "..."

                                    kb_buttons.append([InlineKeyboardButton(
                                        text=button_text,
                                        callback_data=f"key_detail:{payment_id}"
                                    )])

                                kb_buttons.append([
                                    InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys"),
                                    InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")
                                ])

                                kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                                await send_or_edit_message_with_photo(callback, keys_text, reply_markup=kb, edit_message=True, image_name="my_keys.png")
                            else:
                                no_keys_text = "📭 <b>У вас нет ключей ОБХОД глушилок + VPN</b>"
                                if hidden > 0:
                                    no_keys_text += f"\n\n<i>🗑 {hidden} истекших ключ(а) скрыто из списка</i>"
                                await send_or_edit_message_with_photo(callback, no_keys_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")],
                                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                ]), edit_message=True, image_name="my_keys.png")
                        else:
                            await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка загрузки ключей</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys_night")],
                                [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
                            ]), edit_message=True, image_name="my_keys.png")
                    else:
                        await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка API</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="my_keys_night")],
                            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
                        ]), edit_message=True, image_name="my_keys.png")
        except Exception as e:
            logging.error(f"Ошибка показа ключей ОБХОД глушилок + VPN: {e}")
            await send_or_edit_message_with_photo(callback, "❌ <b>Ошибка загрузки ключей</b>\n\nПопробуйте позже.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="my_keys_night")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_keys")]
            ]), edit_message=True, image_name="my_keys.png")
    else:
        await send_or_edit_message_with_photo(callback, "❌ <b>Django интеграция недоступна</b>", edit_message=True, image_name="my_keys.png")


@router.callback_query(F.data.startswith("key_detail:"))
async def show_key_detail(callback: CallbackQuery):
    """Показать детальную информацию о ключе"""
    user_id = callback.from_user.id
    payment_id = int(callback.data.split(":")[1])

    if DJANGO_INTEGRATION:
        try:
            import aiohttp

            # Запрос к API для получения ключей пользователя
            api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get('success') and data.get('keys'):
                            # Ищем ключ по payment_id
                            key_data = None
                            for key in data['keys']:
                                if key.get('payment_id') == payment_id:
                                    key_data = key
                                    break

                            if key_data:
                                key_value = key_data['key_value']
                                subscription_type = key_data.get('subscription_type', '')
                                vpn_type = key_data.get('vpn_type', 'night')
                                
                                # Определяем callback возврата
                                return_callback = "my_keys_regular" if vpn_type == 'regular' else "my_keys_night"

                                # Определяем тип VPN
                                vpn_type_label = "Обычный VPN" if vpn_type == 'regular' else "🛡️ ОБХОД глушилок + VPN"

                                # Определяем тип подписки с учетом regular_ типов
                                if subscription_type == 'trial':
                                    sub_type = "Пробная (3 дня)"
                                elif subscription_type in ['regular_month', 'month']:
                                    sub_type = "1 месяц"
                                elif subscription_type in ['regular_3months', '3months']:
                                    sub_type = "3 месяца"
                                elif subscription_type in ['regular_6months', '6months']:
                                    sub_type = "6 месяцев"
                                elif subscription_type in ['regular_year', 'year']:
                                    sub_type = "1 год"
                                elif subscription_type == 'regular_2years':
                                    sub_type = "2 года"
                                elif subscription_type.startswith('regular_'):
                                    sub_type = subscription_type.replace('regular_', '')
                                else:
                                    sub_type = subscription_type
                                used = key_data['used_activations']
                                total = key_data['total_activations']
                                is_active = key_data['is_active']
                                issued_by_manager = key_data.get('issued_by_manager', False)
                                pending_manager = key_data.get('pending_manager', False)
                                
                                # Форматируем дату покупки
                                from datetime import datetime
                                try:
                                    purchase_date = datetime.fromisoformat(key_data['created_at'].replace('Z', '+00:00'))
                                    formatted_date = purchase_date.strftime('%d.%m.%Y %H:%M')
                                except:
                                    formatted_date = "Неизвестно"
                                
                                # Формируем текст с информацией о ключе
                                key_detail_text = f"🔑 <b>Информация о ключе</b>\n\n"
                                key_detail_text += f"<b>Тип VPN:</b> {vpn_type_label}\n"
                                key_detail_text += f"<b>Подписка:</b> {sub_type}\n\n"

                                if pending_manager:
                                    key_detail_text += "⏳ <b>Ожидает выдачи менеджером</b>\n\n"
                                    key_detail_text += f"📆 <b>Дата покупки:</b> {formatted_date}\n"
                                    key_detail_text += f"💬 <b>Обратитесь к менеджеру:</b> @yamalube61\n"
                                elif issued_by_manager and key_value.startswith("Выдан менеджером"):
                                    key_detail_text += "👤 <b>Для получения ключа обратитесь к менеджеру🧑‍💼@yamalube61</b>"
                                    key_detail_text += f"💬 <b>Извините за неудобства 😞</b>\n"
                                    key_detail_text += f"📆 <b>Дата покупки:</b> {formatted_date}\n"
                                elif issued_by_manager:
                                    key_detail_text += f"🔑 <b>Ключ:</b> {key_value}\n\n"
                                    key_detail_text += f"📆 <b>Дата покупки:</b> {formatted_date}\n"
                                    key_detail_text += f"👤 <b>Выдан менеджером</b>\n"
                                else:
                                    key_detail_text += f"🔑 <b>Ключ:</b> {key_value}\n\n"
                                    key_detail_text += f"📆 <b>Дата покупки:</b> {formatted_date}\n"
                                
                                key_detail_text += "\n💡 <b>Как использовать:</b>\n"
                                key_detail_text += "• Скопируйте ключ и введите в приложении\n"
                                key_detail_text += "• Ключ можно использовать несколько раз\n"
                                key_detail_text += "• При проблемах обращайтесь в поддержку"

                                # Для trial — кнопка купить, для остальных — продлить
                                is_trial = subscription_type in ('trial', 'regular_trial')
                                
                                if is_trial:
                                    kb = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="💳 Купить полную подписку", callback_data="catalog")],
                                        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=return_callback)],
                                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                    ])
                                else:
                                    kb = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data=f"renew_subscription:{payment_id}")],
                                        [InlineKeyboardButton(text="📱 Управление устройствами", callback_data=f"devices_list:{payment_id}")],
                                        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=return_callback)],
                                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                    ])
                                
                                # Отправляем новое сообщение с детальной информацией
                                await callback.message.answer(key_detail_text, parse_mode="HTML", reply_markup=kb)
                                await callback.answer()
                            else:
                                await callback.answer("❌ Ключ не найден", show_alert=True)
                        else:
                            await callback.answer("❌ Ключи не найдены", show_alert=True)
                    else:
                        await callback.answer("❌ Ошибка загрузки данных", show_alert=True)
        except Exception as e:
            logging.error(f"Ошибка получения детальной информации о ключе: {e}")
            await callback.answer("❌ Произошла ошибка", show_alert=True)
    else:
        await callback.answer("❌ Система недоступна", show_alert=True)


# ========== УПРАВЛЕНИЕ УСТРОЙСТВАМИ ==========

@router.callback_query(F.data.startswith("devices_list:"))
async def devices_list_callback(callback: CallbackQuery):
    """Показать список HWID устройств подписки"""
    user_id = callback.from_user.id
    payment_id = int(callback.data.split(":")[1])

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return

    try:
        import aiohttp

        api_url = f'{DJANGO_API_URL}/bot_management/api/subscription/{payment_id}/devices/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                data = await response.json()

                if data.get('success'):
                    devices = data.get('devices', [])
                    text = "📱 <b>Подключенные устройства</b>\n\n"
                    kb_buttons = []

                    if not devices:
                        text += "Нет подключенных устройств."
                    else:
                        for i, dev in enumerate(devices, 1):
                            hwid = dev.get('hwid', '—')
                            platform = dev.get('platform') or 'неизвестно'
                            model = dev.get('deviceModel') or ''
                            os_ver = dev.get('osVersion') or ''
                            info = platform
                            if model:
                                info += f" {model}"
                            if os_ver:
                                info += f" ({os_ver})"
                            text += f"{i}. <code>{hwid[:12]}…</code> — {info}\n"
                            kb_buttons.append([InlineKeyboardButton(
                                text=f"❌ Удалить #{i}",
                                callback_data=f"delete_device:{payment_id}:{hwid}"
                            )])

                    text += "\n<i>Вы можете удалить отдельные устройства или сбросить все.</i>"
                    kb_buttons.append([
                        InlineKeyboardButton(text="🗑 Сбросить все", callback_data=f"reset_devices:{payment_id}"),
                        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"key_detail:{payment_id}"),
                    ])

                    await callback.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))
                    await callback.answer()
                else:
                    await callback.answer(f"❌ {data.get('error', 'Ошибка')}", show_alert=True)

    except Exception as e:
        logging.error(f"Ошибка получения устройств {payment_id}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("delete_device:"))
async def delete_device_callback(callback: CallbackQuery):
    """Удалить конкретное HWID устройство"""
    parts = callback.data.split(":", 2)
    payment_id = int(parts[1])
    hwid = parts[2]

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return

    try:
        import aiohttp
        import json

        api_url = f'{DJANGO_API_URL}/bot_management/api/subscription/{payment_id}/delete-device/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data=json.dumps({'hwid': hwid}), headers={'Content-Type': 'application/json'}) as response:
                data = await response.json()

                if data.get('success'):
                    await callback.answer("✅ Устройство удалено", show_alert=True)
                    # Обновляем список
                    await devices_list_callback(callback)
                else:
                    await callback.answer(f"❌ {data.get('error', 'Ошибка')}", show_alert=True)

    except Exception as e:
        logging.error(f"Ошибка удаления устройства {payment_id}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("reset_devices:"))
async def reset_devices_callback(callback: CallbackQuery):
    """Сбросить все HWID устройства подписки"""
    payment_id = int(callback.data.split(":")[1])

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return

    try:
        import aiohttp

        api_url = f'{DJANGO_API_URL}/bot_management/api/subscription/{payment_id}/reset-devices/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url) as response:
                data = await response.json()

                if data.get('success'):
                    await callback.answer("✅ Все устройства сброшены", show_alert=True)
                    await devices_list_callback(callback)
                else:
                    error = data.get('error', data.get('message', 'Неизвестная ошибка'))
                    await callback.answer(f"❌ {error}", show_alert=True)

    except Exception as e:
        logging.error(f"Ошибка сброса устройств {payment_id}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ========== ПРОДЛЕНИЕ ПОДПИСКИ ==========

@router.callback_query(F.data.startswith("renew_subscription:"))
async def renew_subscription_click(callback: CallbackQuery):
    """Обработчик нажатия кнопки 'Продлить подписку'"""
    user_id = callback.from_user.id
    payment_id = int(callback.data.split(":")[1])

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Django интеграция недоступна", show_alert=True)
        return

    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await callback.answer("❌ Ошибка загрузки данных", show_alert=True)
                    return

                data = await response.json()
                if not data.get('success') or not data.get('keys'):
                    await callback.answer("❌ Ключи не найдены", show_alert=True)
                    return

                # Ищем платеж
                payment_data = None
                for key in data['keys']:
                    if key.get('payment_id') == payment_id:
                        payment_data = key
                        break

                if not payment_data:
                    await callback.answer("❌ Платеж не найден", show_alert=True)
                    return

                vpn_type = payment_data.get('vpn_type', 'night')
                subscription_type = payment_data.get('subscription_type', '')
                
                # Проверяем что это не триал
                if subscription_type == 'trial':
                    await callback.answer("❌ Пробную подписку нельзя продлить, оформите платную", show_alert=True)
                    return

                # Определяем цену и название
                is_regular = vpn_type == 'regular'
                
                if is_regular:
                    from config import REGULAR_VPN_PRICES
                    prices = REGULAR_VPN_PRICES
                    sub_names = {
                        'day': '1 день',
                        'month': '1 месяц',
                        '3months': '3 месяца',
                        '6months': '6 месяцев',
                        'year': '1 год',
                        '2years': '2 года',
                    }
                    vpn_label = "Обычный VPN"
                else:
                    # ОБХОД глушилок + VPN - получаем цены из API
                    from config import PRICES
                    prices = PRICES
                    try:
                        async with aiohttp.ClientSession() as session2:
                            async with session2.get(f'{DJANGO_API_URL}/bot_management/api/prices/get/') as resp2:
                                if resp2.status == 200:
                                    prices_data = await resp2.json()
                                    prices = prices_data.get('prices', PRICES)
                    except:
                        from config import PRICES
                        prices = PRICES
                    
                    sub_names = {
                        'week': '1 неделя',
                        'month': 'Месячная подписка',
                        '3months': 'Подписка на 3 месяца',
                        '6months': 'Подписка на 6 месяцев',
                        'year': 'Годовая подписка',
                    }
                    vpn_label = "🛡️ ОБХОД глушилок + VPN"

                amount = prices.get(subscription_type, 0)
                sub_name = sub_names.get(subscription_type, subscription_type)

                # Проверка подписки на канал отключена
                
                # Предлагаем способы оплаты
                renew_text = f"""
💳 <b>Продление подписки</b>

{vpn_label}
📅 <b>Подписка:</b> {sub_name}
💰 <b>Сумма:</b> {amount} ₽

<i>Выберите способ оплаты:</i>
"""
                cryptobot_available = CRYPTOBOT_API_TOKEN and len(CRYPTOBOT_API_TOKEN.strip()) > 0

                kb_buttons = [
                    [InlineKeyboardButton(text="📱 СБП (QR-код)", callback_data=f"renew_pay_sbp_{payment_id}")],
                    [InlineKeyboardButton(text="💳 Банковской картой", callback_data=f"renew_pay_bank_card_{payment_id}")],
                    [InlineKeyboardButton(text="₿ Криптовалютой (Platega)", callback_data=f"renew_pay_crypto_{payment_id}")],
                ]

                if cryptobot_available:
                    kb_buttons.append([InlineKeyboardButton(text="₿ Криптовалютой (CryptoBot)", callback_data=f"renew_pay_cryptobot_{payment_id}")])

                kb_buttons.extend([
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=f"key_detail:{payment_id}")],
                ])

                kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

                await callback.message.answer(renew_text, parse_mode="HTML", reply_markup=kb)
                await callback.answer()

    except Exception as e:
        logging.error(f"Ошибка при открытии продления подписки: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# Обработчики оплаты для продления
@router.callback_query(F.data.startswith("renew_pay_sbp_"))
async def renew_pay_sbp(callback: CallbackQuery):
    """Оплата продления через СБП (QR-код)"""
    payment_id = int(callback.data.split("_")[-1])
    await process_renewal_payment(callback, payment_id, "sbp")


@router.callback_query(F.data.startswith("renew_pay_bank_card_"))
async def renew_pay_bank_card(callback: CallbackQuery):
    """Оплата продления банковской картой"""
    payment_id = int(callback.data.split("_")[-1])
    await process_renewal_payment(callback, payment_id, "bank_card")


@router.callback_query(F.data.startswith("renew_pay_crypto_"))
async def renew_pay_crypto(callback: CallbackQuery):
    """Оплата продления через Platega крипто"""
    payment_id = int(callback.data.split("_")[-1])
    await process_renewal_payment(callback, payment_id, "crypto")


@router.callback_query(F.data.startswith("renew_pay_cryptobot_"))
async def renew_pay_cryptobot(callback: CallbackQuery):
    """Оплата продления через CryptoBot"""
    payment_id = int(callback.data.split("_")[-1])
    await process_renewal_payment_cryptobot(callback, payment_id)


async def process_renewal_payment(callback: CallbackQuery, payment_id: int, method: str):
    """Создает платеж для продления через Platega/Antilopay"""
    user_id = callback.from_user.id

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Django интеграция недоступна", show_alert=True)
        return

    try:
        import aiohttp
        # Получаем данные о предыдущем платеже
        api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await callback.answer("❌ Ошибка загрузки данных", show_alert=True)
                    return

                data = await response.json()
                if not data.get('success') or not data.get('keys'):
                    await callback.answer("❌ Ключи не найдены", show_alert=True)
                    return

                old_payment = None
                for key in data['keys']:
                    if key.get('payment_id') == payment_id:
                        old_payment = key
                        break

                if not old_payment:
                    await callback.answer("❌ Платеж не найден", show_alert=True)
                    return

                vpn_type = old_payment.get('vpn_type', 'night')
                subscription_type = old_payment.get('subscription_type', '')

                # Определяем цену
                if vpn_type == 'regular':
                    from config import REGULAR_VPN_PRICES
                    amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
                elif vpn_type == 'fast':
                    from config import FAST_VPN_PRICES
                    amount = FAST_VPN_PRICES.get(subscription_type, 0)
                else:
                    from config import PRICES
                    try:
                        async with aiohttp.ClientSession() as s2:
                            async with s2.get(f'{DJANGO_API_URL}/bot_management/api/prices/get/') as r2:
                                if r2.status == 200:
                                    pd = await r2.json()
                                    amount = pd.get('prices', PRICES).get(subscription_type, 0)
                                else:
                                    amount = PRICES.get(subscription_type, 0)
                    except:
                        from config import PRICES
                        amount = PRICES.get(subscription_type, 0)

                # Для СБП используем Antilopay, для остальных — Platega
                if method == 'sbp':
                    create_url = f'{DJANGO_API_URL}/bot_management/api/payments/create-antilopay/'
                else:
                    create_url = f'{DJANGO_API_URL}/bot_management/api/payments/create/'

                payment_data = {
                    'user_id': user_id,
                    'subscription_type': subscription_type,
                    'vpn_type': vpn_type,
                    'amount': amount,
                    'is_renewal': True,
                    'renewal_for_payment_id': payment_id,
                }

                async with aiohttp.ClientSession() as session2:
                    async with session2.post(create_url, json=payment_data) as resp2:
                        if resp2.status != 200:
                            error_text = await resp2.text()
                            logging.error(f"Ошибка создания платежа продления ({method}): {resp2.status} - {error_text}")
                            await callback.answer(f"❌ Ошибка: {resp2.status}", show_alert=True)
                            return

                        result = await resp2.json()
                        logging.info(f"Результат создания платежа продления: {result}")
                        
                        is_ok = result.get('success') or result.get('status') == 'success'
                        if not is_ok:
                            await callback.answer(f"❌ {result.get('error', result.get('message', 'Ошибка'))}", show_alert=True)
                            return

                        payment_url = result.get('payment_url') or result.get('confirmation_url')
                        transaction_id = result.get('transaction_id') or result.get('invoice_id')
                        new_payment_id = result.get('payment_id')

                        if not payment_url:
                            await callback.answer("❌ Не получена ссылка для оплаты", show_alert=True)
                            return

                        # Отправляем ссылку на оплату
                        from config import REQUIRED_CHANNEL
                        text = f"""
💳 <b>Оплата продления подписки</b>

📋 <b>Транзакция:</b> <code>{transaction_id}</code>
💰 <b>Сумма:</b> {amount} ₽

🔗 <b>Для оплаты перейдите по ссылке:</b>
<a href="{payment_url}">Оплатить {amount} ₽</a>

⏰ <i>Оплата действительна в течение 30 минут</i>

<i>После оплаты нажмите "Проверить платёж" для активации</i>
"""
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💳 Оплатить", url=payment_url)],
                            [InlineKeyboardButton(text="✅ Проверить платёж", callback_data=f"check_renewal_payment:{new_payment_id}")],
                            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"key_detail:{payment_id}")],
                            [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")],
                        ])

                        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                        await callback.answer()

    except Exception as e:
        logging.error(f"Ошибка создания платежа для продления: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


async def process_renewal_payment_cryptobot(callback: CallbackQuery, payment_id: int):
    """Создает платеж для продления через CryptoBot"""
    user_id = callback.from_user.id

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Django интеграция недоступна", show_alert=True)
        return

    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/users/{user_id}/keys/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await callback.answer("❌ Ошибка загрузки данных", show_alert=True)
                    return

                data = await response.json()
                old_payment = None
                for key in data.get('keys', []):
                    if key.get('payment_id') == payment_id:
                        old_payment = key
                        break

                if not old_payment:
                    await callback.answer("❌ Платеж не найден", show_alert=True)
                    return

                vpn_type = old_payment.get('vpn_type', 'night')
                subscription_type = old_payment.get('subscription_type', '')

                if vpn_type == 'regular':
                    from config import REGULAR_VPN_PRICES
                    amount = REGULAR_VPN_PRICES.get(subscription_type, 0)
                else:
                    from config import PRICES
                    amount = PRICES.get(subscription_type, 0)

                # Создаем платеж CryptoBot
                create_url = f'{DJANGO_API_URL}/bot_management/api/payments/create-cryptobot/'
                payment_data = {
                    'user_id': user_id,
                    'subscription_type': subscription_type,
                    'vpn_type': vpn_type,
                    'amount': amount,
                    'is_renewal': True,
                    'renewal_for_payment_id': payment_id,
                }

                async with aiohttp.ClientSession() as session2:
                    async with session2.post(create_url, json=payment_data) as resp2:
                        if resp2.status != 200:
                            error_text = await resp2.text()
                            logging.error(f"Ошибка создания платежа продления ({method}): {resp2.status} - {error_text}")
                            await callback.answer(f"❌ Ошибка: {resp2.status}", show_alert=True)
                            return

                        result = await resp2.json()
                        logging.info(f"Результат создания платежа продления CryptoBot: {result}")
                        
                        is_ok = result.get('success') or result.get('status') == 'success'
                        if not is_ok:
                            await callback.answer(f"❌ {result.get('error', result.get('message', 'Ошибка'))}", show_alert=True)
                            return

                        payment_url = result.get('payment_url') or result.get('confirmation_url')
                        invoice_id = result.get('invoice_id') or result.get('transaction_id')
                        new_payment_id = result.get('payment_id')

                        if not payment_url:
                            await callback.answer("❌ Не получена ссылка для оплаты", show_alert=True)
                            return

                        from config import REQUIRED_CHANNEL
                        text = f"""
₿ <b>Оплата продления через CryptoBot</b>

📋 <b>Инвойс:</b> <code>{invoice_id}</code>
💰 <b>Сумма:</b> эквивалент {amount} ₽ в криптовалюте

🔗 <b>Для оплаты перейдите по ссылке:</b>
<a href="{payment_url}">Оплатить через CryptoBot</a>

⏰ <i>Оплата действительна в течение 60 минут</i>

<i>После оплаты нажмите "Проверить платёж" для активации</i>
"""
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="₿ Оплатить", url=payment_url)],
                            [InlineKeyboardButton(text="✅ Проверить платёж", callback_data=f"check_renewal_payment:{new_payment_id}")],
                            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"key_detail:{payment_id}")],
                            [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")],
                        ])

                        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                        await callback.answer()

    except Exception as e:
        logging.error(f"Ошибка создания CryptoBot платежа для продления: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ========== ПРОВЕРКА ОПЛАТЫ ПРОДЛЕНИЯ ==========

@router.callback_query(F.data.startswith("check_renewal_payment:"))
async def check_renewal_payment(callback: CallbackQuery):
    """Проверка оплаты продления подписки — аналог check_payment_status"""
    user_id = callback.from_user.id
    renewal_payment_id = int(callback.data.split(":")[1])

    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Django интеграция недоступна", show_alert=True)
        return

    try:
        import aiohttp
        import json

        # Получаем данные платежа чтобы определить тип
        api_url_basic = f'{DJANGO_API_URL}/bot_management/api/payments/{renewal_payment_id}/status/'

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url_basic) as basic_response:
                if basic_response.status != 200:
                    await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
                    return

                basic_data = await basic_response.json()
                if 'error' in basic_data:
                    await callback.answer(f"❌ {basic_data.get('error', 'Ошибка проверки')}", show_alert=True)
                    return

                platega_id = basic_data.get('platega_transaction_id')
                cryptobot_id = basic_data.get('cryptobot_invoice_id')
                antilopay_id = basic_data.get('antilopay_payment_id')
                payment_status = basic_data.get('status', 'unknown')
                issued_key = basic_data.get('issued_key')

        # Если ключ уже выдан — отправляем пользователю
        if payment_status == 'succeeded' and issued_key:
            vpn_type = basic_data.get('vpn_type', 'night')
            subscription_type = basic_data.get('subscription_type', '')
            await _send_key_message(callback, issued_key, vpn_type, subscription_type)
            return

        # Определяем тип платежа и проверяем нужный API
        if cryptobot_id:
            api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{renewal_payment_id}/cryptobot-status/'
            payment_type = 'cryptobot'
        elif antilopay_id:
            api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{renewal_payment_id}/antilopay-status/'
            payment_type = 'antilopay'
        elif platega_id:
            api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{renewal_payment_id}/platega-status/'
            payment_type = 'platega'
        else:
            await callback.answer("❌ Неизвестный тип платежа. Обратитесь в поддержку", show_alert=True)
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await callback.answer("❌ Ошибка проверки статуса", show_alert=True)
                    return

                data = await response.json()

                if not data.get('success'):
                    error_message = data.get('message', 'Ошибка проверки статуса')
                    await callback.answer(f"❌ {error_message}", show_alert=True)
                    return

                if payment_type == 'cryptobot':
                    raw_status = data.get('cryptobot_status', 'unknown')
                    status_normalized = raw_status.lower() if raw_status else 'unknown'
                elif payment_type == 'antilopay':
                    raw_status = data.get('antilopay_status', 'unknown')
                    status_normalized = raw_status.upper() if raw_status else 'UNKNOWN'
                else:
                    raw_status = data.get('platega_status', 'unknown')
                    status_normalized = raw_status.upper() if raw_status else 'UNKNOWN'

                payment_status = data.get('payment_status', 'unknown')
                issued_key = data.get('issued_key')

                # Проверяем успешную оплату
                is_paid = (payment_type == 'cryptobot' and status_normalized == 'paid') or \
                          (payment_type == 'platega' and status_normalized == 'CONFIRMED') or \
                          (payment_type == 'antilopay' and status_normalized == 'SUCCESS')

                if is_paid and payment_status == 'succeeded':
                    if issued_key:
                        vpn_type = data.get('vpn_type', 'night')
                        subscription_type = data.get('subscription_type', '')
                        await _send_key_message(callback, issued_key, vpn_type, subscription_type)
                    else:
                        await callback.answer("⏳ Платеж подтвержден, запускаем выдачу ключа...", show_alert=True)
                        await _try_issue_key_on_payment_confirm(renewal_payment_id, callback)
                elif is_paid and payment_status != 'succeeded':
                    await callback.answer("⏳ Платеж подтвержден, запускаем выдачу ключа...", show_alert=True)
                    await _try_issue_key_on_payment_confirm(renewal_payment_id, callback)
                elif status_normalized in ('active', 'pending', 'PENDING'):
                    await callback.answer("⏳ Платеж еще не оплачен. Попробуйте позже.", show_alert=True)
                elif status_normalized in ('expired', 'CANCELED'):
                    action_text = "истек" if status_normalized == 'expired' else "отменен"
                    await callback.answer(f"❌ Платеж {action_text}. Создайте новый.", show_alert=True)
                else:
                    await callback.answer(f"❓ Статус платежа: {raw_status}", show_alert=True)

    except Exception as e:
        logging.error(f"Ошибка проверки платежа продления: {e}")
        import traceback
        logging.error(traceback.format_exc())
        await callback.answer("⚠️ Ошибка проверки. Обратитесь в поддержку: @yamalube61", show_alert=True)


# Обработчик профиля
@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    """Показать профиль пользователя"""
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            profile_data = await get_user_profile(user_id)
            
            if profile_data and profile_data.get('success'):
                user_info = profile_data.get('user', {})
                referrals_count = profile_data.get('referrals_count', 0)
                total_rewards = profile_data.get('total_rewards', 0)
                referral_code = profile_data.get('referral_code', 'Не создан')
                referral_balance = user_info.get('referral_balance', 0)
                
                # Форматируем дату регистрации
                from datetime import datetime
                try:
                    reg_date = datetime.fromisoformat(user_info.get('created_at', '').replace('Z', '+00:00'))
                    formatted_date = reg_date.strftime('%d.%m.%Y %H:%M')
                except:
                    formatted_date = "Неизвестно"
                
                # Форматируем имя - если есть фамилия, добавляем её
                first_name = user_info.get('first_name', 'Не указано')
                last_name = user_info.get('last_name') or ''
                if last_name and last_name != 'None' and last_name.strip():
                    full_name = f"{first_name} {last_name}"
                else:
                    full_name = first_name
                
                profile_text = f"""
👤 <b>Профиль пользователя</b>

🆔 <b>ID:</b> {user_info.get('user_id', user_id)}
📱 <b>Username:</b> @{user_info.get('username', 'Не указан')}
📅 <b>Дата регистрации:</b> {formatted_date}

👥 <b>Реферальная программа:</b>
🎯 <b>Ваш код:</b> {referral_code}
📊 <b>Приглашено друзей:</b> {referrals_count}
💰 <b>Реферальный баланс:</b> {referral_balance} ₽

🔗 <b>Поделиться кодом:</b>
https://t.me/webnetvpn_robot?start={referral_code}

⬇️<b>Выберите действия  из списка ниже</b>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await send_or_edit_message_with_photo(callback, profile_text, reply_markup=kb, edit_message=True, image_name="profile.png")
            else:
                error_text = """
👤 <b>Профиль пользователя</b>

❌ <b>Ошибка загрузки профиля</b>

🔧 <b>Что происходит:</b>
• Временная недоступность системы
• Попробуйте позже

⏰ <b>Попробуйте через несколько минут</b>
"""
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="profile")],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                ])
                
                await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="profile.png")
                
        except Exception as e:
            logging.error(f"Ошибка получения профиля: {e}")
            error_text = """
👤 <b>Профиль пользователя</b>

❌ <b>Ошибка загрузки профиля</b>

🔧 <b>Что происходит:</b>
• Временная недоступность системы
• Попробуйте позже

⏰ <b>Попробуйте через несколько минут</b>
"""
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="profile")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ])
            
            await send_or_edit_message_with_photo(callback, error_text, reply_markup=kb, edit_message=True, image_name="profile.png")
    else:
        no_integration_text = """
👤 <b>Профиль пользователя</b>

❌ <b>Система недоступна</b>

🔧 <b>Что происходит:</b>
• Django интеграция не настроена
• Обратитесь к администратору

⏰ <b>Попробуйте позже</b>
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])
        
        await send_or_edit_message_with_photo(callback, no_integration_text, reply_markup=kb, edit_message=True, image_name="profile.png")


# Обработчик баланса отключен - покупки теперь напрямую
@router.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    """Баланс отключен - перенаправление на главное меню"""
    await main_menu(callback)


# Обработчик пополнения баланса
# Обработчик пополнения баланса отключен - покупки теперь напрямую
@router.callback_query(F.data == "deposit_balance")
async def deposit_balance(callback: CallbackQuery):
    """Пополнение баланса отключено - перенаправление на главное меню"""
    await main_menu(callback)

# Обработчики кнопок с суммами отключены - покупки теперь напрямую
@router.callback_query(F.data.startswith("deposit_"))
async def handle_deposit_amount(callback: CallbackQuery):
    """Пополнение баланса отключено - перенаправление на главное меню"""
    await main_menu(callback)

# Обработчик reply сообщений (продолжение общения) - ДОЛЖЕН БЫТЬ ПЕРВЫМ!
@router.message(F.reply_to_message)
@protect_message('messages')
async def handle_reply_message(message: Message, state: FSMContext):
    """Обработка reply сообщений для продолжения общения с поддержкой"""
    # Если пользователь в состоянии поиска рефереров — передаём сообщение в обработчик поиска
    current_state = await state.get_state()
    if current_state == ReferrersSearchState.waiting_search.state:
        await handle_referrers_search(message, state)
        return
    if current_state and current_state.startswith("AdminPromoState:"):
        await admin_promo_code_input(message, state)
        return
    if current_state and current_state.startswith("PromoState:"):
        await promo_code_input_handler(message, state)
        return

    user_id = message.from_user.id
    reply_to = message.reply_to_message

    logging.info(f"DEBUG: Reply сообщение от пользователя {user_id}")
    logging.info(f"DEBUG: Reply to message: {reply_to.text if reply_to and reply_to.text else 'Нет текста'}")
    logging.info(f"DEBUG: Reply to user ID: {reply_to.from_user.id if reply_to else 'Нет reply_to'}")
    logging.info(f"DEBUG: Bot ID: {bot.id}")
    
    # Проверяем, является ли это ответом на сообщение от поддержки
    if reply_to and reply_to.from_user.id == bot.id:
        logging.info("DEBUG: Это ответ на сообщение от бота")
        
        # Проверяем текст сообщения
        reply_text = reply_to.text or ""
        reply_caption = reply_to.caption or ""
        
        logging.info(f"DEBUG: Текст сообщения: {reply_text}")
        logging.info(f"DEBUG: Подпись сообщения: {reply_caption}")
        
        # Проверяем, содержит ли сообщение текст о поддержке (в тексте или подписи)
        is_support_message = (
            ("💬 Ответ от поддержки:" in reply_text) or 
            ("📸 Фото от поддержки:" in reply_text) or
            ("💬 Ответ от поддержки:" in reply_caption) or 
            ("📸 Фото от поддержки:" in reply_caption) or
            # Альтернативные способы определения сообщений поддержки
            ("поддержки:" in reply_text.lower()) or
            ("поддержки:" in reply_caption.lower()) or
            ("💬" in reply_text) or
            ("📸" in reply_text) or
            ("💬" in reply_caption) or
            ("📸" in reply_caption) or
            # Если это фото от бота с любой подписью - считаем сообщением поддержки
            (reply_to.photo and reply_caption) or
            # Если это текстовое сообщение от бота с эмодзи - считаем сообщением поддержки
            (reply_text and ("💬" in reply_text or "📸" in reply_text or "🛠" in reply_text))
        )
        
        if is_support_message:
            logging.info("DEBUG: Это ответ на сообщение поддержки!")
            logging.info(f"DEBUG: Причина: текст='{reply_text}', подпись='{reply_caption}'")
            
            # НЕ обрабатываем автоматически - просим нажать кнопку
            await message.answer("""
💬 <b>Чтобы ответить поддержке:</b>

🔘 <b>Нажмите кнопку "💬 Ответить поддержке"</b> под сообщением от поддержки

📝 <b>После нажатия кнопки</b> вы сможете написать ответ

<i>Это нужно для правильной обработки вашего сообщения</i>
""")
            return
        else:
            logging.info("DEBUG: Это не сообщение поддержки")
            logging.info(f"DEBUG: Причина: текст='{reply_text}', подпись='{reply_caption}'")
    else:
        logging.info("DEBUG: Это не ответ на сообщение от бота")
    
    # Проверка состояния поддержки отключена - поддержка теперь через прямую ссылку
    # current_state = await state.get_state()
    # if current_state == SupportState.waiting_for_message:
    #     logging.info("DEBUG: Пользователь уже в состоянии поддержки")
    #     # Пользователь уже в чате поддержки
    #     if message.photo:
    #         await handle_support_photo(message, state)
    #     else:
    #         await handle_support_message(message, state)

# Обработчик загрузки ключей (должен быть ПЕРЕД обработчиками с фильтрами текста)
@router.message(UploadKeysState.waiting_keys)
async def process_upload_keys_early(message: Message, state: FSMContext):
    """Обработка загруженных ключей - ранняя версия для приоритета"""
    logging.info(f"DEBUG: Получено сообщение для загрузки ключей от {message.from_user.id}")
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет доступа")
        await state.clear()
        return
    
    # Проверяем отмену
    if message.text and message.text.strip() == "/cancel":
        await message.answer("❌ Загрузка отменена")
        await state.clear()
        return
    
    keys_text = message.text or ""
    
    if not keys_text.strip():
        await message.answer("❌ Список ключей не может быть пустым. Отправьте ключи или /cancel для отмены.")
        return
    
    # Получаем сохраненные данные
    data = await state.get_data()
    subscription_type = data.get('subscription_type')
    total_activations = data.get('total_activations', 1)
    
    if not subscription_type:
        await message.answer("❌ Ошибка: тип подписки не выбран. Начните заново.")
        await state.clear()
        return
    
    # Отправляем ключи на сервер
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/keys/upload/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={
                'keys': keys_text,
                'subscription_type': subscription_type,
                'total_activations': total_activations
            }) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        created = result.get('created', 0)
                        skipped = result.get('skipped', 0)
                        errors = result.get('errors')
                        
                        result_text = f"""
✅ <b>Ключи загружены!</b>

📊 <b>Результат:</b>
• ✅ Создано: <b>{created}</b>
• ⏭️ Пропущено: <b>{skipped}</b>
"""
                        if errors:
                            result_text += f"\n❌ <b>Ошибки:</b> {len(errors)}\n"
                            for error in errors[:5]:  # Показываем первые 5 ошибок
                                result_text += f"• {error}\n"
                            if len(errors) > 5:
                                result_text += f"<i>... и еще {len(errors) - 5} ошибок</i>\n"
                        
                        await message.answer(result_text)
                    else:
                        await message.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}")
                else:
                    error_text = await response.text()
                    await message.answer(f"❌ Ошибка сервера: {error_text}")
    except Exception as e:
        logging.error(f"Ошибка загрузки ключей: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
    
    await state.clear()

# Обработчик ввода произвольной суммы
@router.message(F.text.regexp(r'^\d+$'))
async def handle_custom_deposit_amount(message: Message, state: FSMContext):
    """Обработка ввода произвольной суммы - только для вывода реферальных средств"""
    # Пропускаем, если пользователь в состоянии рассылки или загрузки ключей
    current_state = await state.get_state()
    if current_state == BroadcastState.waiting_message or current_state == BroadcastState.waiting_confirmation:
        return
    
    if current_state == UploadKeysState.waiting_keys:
        return
    
    if current_state and current_state.startswith("AdminPromoState:"):
        if current_state == "AdminPromoState:waiting_discount":
            await admin_promo_discount_input(message, state)
        elif current_state == "AdminPromoState:waiting_max_per_user":
            await admin_promo_max_per_user_input(message, state)
        elif current_state == "AdminPromoState:waiting_max_uses":
            await admin_promo_max_uses_input(message, state)
        return
    
    if current_state and current_state.startswith("PromoState:"):
        if current_state == "PromoState:waiting_code":
            await promo_code_input_handler(message, state)
        return
    
    # Пропускаем проверку спама для FSM состояний вывода средств
    if current_state in [WithdrawalState.waiting_amount, WithdrawalState.waiting_payment_details]:
        # Передаем управление соответствующим обработчикам
        if current_state == WithdrawalState.waiting_amount:
            await handle_withdrawal_amount(message, state)
        elif current_state == WithdrawalState.waiting_payment_details:
            await handle_withdrawal_details(message, state)
        return
    
    try:
        # Проверяем, не находимся ли мы в процессе вывода средств
        current_state = await state.get_state()
        if current_state == WithdrawalState.waiting_amount:
            # Если мы ждем сумму для вывода, передаем управление соответствующему обработчику
            await handle_withdrawal_amount(message, state)
            return
        elif current_state == WithdrawalState.waiting_payment_details:
            # Если мы ждем реквизиты для вывода, передаем управление соответствующему обработчику
            await handle_withdrawal_details(message, state)
            return
        
        # Пополнение баланса отключено - игнорируем ввод суммы
        # await message.answer("💡 Для покупки подписки выберите её в каталоге")
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректную сумму (только цифры)")

# Старый обработчик FSM удален - теперь используем простые кнопки


# Обработчики рефералов
@router.callback_query(F.data == "referral")
async def referral_menu(callback: CallbackQuery):
    """Меню рефералов"""
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            # Получаем информацию о боте
            bot_info = await bot.get_me()
            bot_username = bot_info.username
            
            # Получаем статистику рефералов
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/stats/{user_id}/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get('success'):
                            referrals_count = data.get('referrals_count', 0)
                            total_purchases = data.get('total_purchases', 0)
                            total_revenue = data.get('total_revenue', 0)
                            total_commission = data.get('total_commission', 0)
                            commission_percent = data.get('commission_percent', 20)
                            referral_code = data.get('referral_code')
                            referral_balance = data.get('referral_balance', 0)
                            
                            if referral_code:
                                text = f"""
👥 <b>Реферальная программа</b>

🎯 <b>Ваш реферальный код:</b> {referral_code}

📊 <b>Статистика:</b>
• Приглашено друзей: {referrals_count}
• Всего покупок: {total_purchases}
• Общая выручка: {total_revenue:.2f} ₽
• Ваша комиссия ({commission_percent}%): {total_commission:.2f} ₽
• Реферальный баланс: {referral_balance:.2f} ₽

💰 <b>Как заработать:</b>
• Поделитесь своим кодом с друзьями
• Получайте {commission_percent}% с каждой их покупки
• Деньги начисляются автоматически

🔗 <b>Поделиться кодом:</b>
https://t.me/{bot_username}?start={referral_code}

<i>Приглашайте друзей и зарабатывайте вместе!</i>
"""
                            else:
                                # Создаем реферальный код
                                create_url = f'{DJANGO_API_URL}/bot_management/api/referral/create/'
                                async with session.post(create_url, data={'user_id': user_id}) as create_response:
                                    if create_response.status == 200:
                                        create_data = await create_response.json()
                                        if create_data.get('success'):
                                            referral_code = create_data.get('code')
                                            
                                            text = f"""
👥 <b>Реферальная программа</b>

🎯 <b>Ваш реферальный код:</b> {referral_code}

📊 <b>Статистика:</b>
• Приглашено друзей: 0
• Всего покупок: 0
• Общая выручка: 0.00 ₽
• Ваша комиссия (20%): 0.00 ₽
• Реферальный баланс: {referral_balance:.2f} ₽

💰 <b>Как заработать:</b>
• Поделитесь своим кодом с друзьями
• Получайте 20% с каждой их покупки
• Деньги начисляются автоматически

🔗 <b>Поделиться кодом:</b>
https://t.me/{bot_username}?start={referral_code}

<i>Приглашайте друзей и зарабатывайте вместе!</i>
"""
                                        else:
                                            text = "❌ Ошибка создания реферального кода"
                                    else:
                                        text = "❌ Ошибка сервера"
                        else:
                            text = "❌ Ошибка получения статистики"
                    else:
                        text = "❌ Ошибка API"
        except Exception as e:
            print(f"Ошибка получения статистики рефералов: {e}")
            text = "❌ Ошибка загрузки данных"
    else:
        text = "❌ Система недоступна"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, text, reply_markup=kb, edit_message=True)


# Обработчик информации о реферальной системе
@router.callback_query(F.data == "referral_info")
async def referral_info_handler(callback: CallbackQuery):
    """Информация о реферальной системе"""
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            # Получаем информацию о боте
            bot_info = await bot.get_me()
            bot_username = bot_info.username
            
            # Получаем статистику рефералов
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/stats/{user_id}/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get('success'):
                            referrals_count = data.get('referrals_count', 0)
                            total_purchases = data.get('total_purchases', 0)
                            total_revenue = data.get('total_revenue', 0)
                            total_commission = data.get('total_commission', 0)
                            commission_percent = data.get('commission_percent', 20)
                            referral_code = data.get('referral_code')
                            referral_balance = data.get('referral_balance', 0)
                            
                            if not referral_code:
                                # Создаем реферальный код если его нет
                                create_url = f'{DJANGO_API_URL}/bot_management/api/referral/create/'
                                async with session.post(create_url, data={'user_id': user_id}) as create_response:
                                    if create_response.status == 200:
                                        create_data = await create_response.json()
                                        if create_data.get('success'):
                                            referral_code = create_data.get('code')
                            
                            info_text = f"""
🎯 <b>Реферальная система</b>

💰 <b>Как это работает:</b>
• Приглашайте друзей по своему коду
• Вы получаете <b>{commission_percent}% от покупки вашими рефералами</b>
• Деньги начисляются автоматически

📊 <b>Ваша статистика:</b>
🎯 <b>Ваш код:</b> {referral_code or 'Не создан'}
👥 <b>Приглашено друзей:</b> {referrals_count}
🛒 <b>Всего покупок:</b> {total_purchases}
💵 <b>Общая выручка:</b> {total_revenue:.2f} ₽
💰 <b>Ваша комиссия ({commission_percent}%):</b> {total_commission:.2f} ₽
💳 <b>Реферальный баланс:</b> {referral_balance:.2f} ₽

🔗 <b>Поделиться кодом:</b>
https://t.me/{bot_username}?start={referral_code or 'YOUR_CODE'}

💡 <b>Советы:</b>
• Делитесь кодом в социальных сетях
• Рассказывайте друзьям о преимуществах
• Чем больше друзей - тем больше заработок!

<i>Приглашайте друзей и зарабатывайте вместе! 🚀</i>
"""
                        else:
                            info_text = "❌ Ошибка получения статистики"
                    else:
                        info_text = "❌ Ошибка API"
        except Exception as e:
            logging.error(f"Ошибка получения информации о рефералах: {e}")
            info_text = "❌ Ошибка загрузки данных"
    else:
        info_text = "❌ Система недоступна"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
         [InlineKeyboardButton(text="📤 Вывести реферальные средства", callback_data="referral_withdrawal")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, info_text, reply_markup=kb, edit_message=True, image_name="referral.png")


# Обработчик вывода реферальных средств
@router.callback_query(F.data == "referral_withdrawal")
async def referral_withdrawal_handler(callback: CallbackQuery):
    """Меню вывода реферальных средств"""
    user_id = callback.from_user.id
    
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            # Получаем реферальный баланс пользователя
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/balance/{user_id}/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get('success'):
                            referral_balance = data.get('referral_balance', 0)
                            transactions = data.get('transactions', [])
                            
                            # Проверяем минимальную сумму для вывода
                            min_withdrawal = 500
                            
                            if referral_balance < min_withdrawal:
                                text = f"""
💰 <b>Реферальный баланс</b>

💳 <b>Ваш реферальный баланс:</b> {referral_balance} ₽

⚠️ <b>Минимальная сумма для вывода:</b> {min_withdrawal} ₽

💡 <b>Продолжайте приглашать друзей, чтобы заработать больше!</b>
"""
                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="👥 Реферальная система", callback_data="referral_info")],
                                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                ])
                            else:
                                # Получаем историю запросов на вывод
                                withdrawal_api_url = f'{DJANGO_API_URL}/bot_management/api/referral/withdrawal/status/{user_id}/'
                                async with session.get(withdrawal_api_url) as withdrawal_response:
                                    if withdrawal_response.status == 200:
                                        withdrawal_data = await withdrawal_response.json()
                                        
                                        if withdrawal_data.get('success'):
                                            withdrawals = withdrawal_data.get('withdrawals', [])
                                            active_withdrawals = [w for w in withdrawals if w['status_code'] in ['pending', 'approved']]
                                            
                                            if active_withdrawals:
                                                text = f"""
💰 <b>Реферальный баланс</b>

💳 <b>Ваш реферальный баланс:</b> {referral_balance} ₽

⚠️ <b>У вас есть активный запрос на вывод средств</b>

📋 <b>Последние запросы:</b>
"""
                                                for withdrawal in withdrawals[:3]:
                                                    status_emoji = {
                                                        'pending': '⏳',
                                                        'approved': '✅',
                                                        'completed': '✅',
                                                        'rejected': '❌'
                                                    }
                                                    emoji = status_emoji.get(withdrawal['status_code'], '❓')
                                                    text += f"{emoji} {withdrawal['amount']} ₽ - {withdrawal['status']}\n"
                                                
                                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                                ])
                                            else:
                                                text = f"""
💰 <b>Вывод реферальных средств</b>

💳 <b>Ваш реферальный баланс:</b> {referral_balance} ₽
💵 <b>Минимальная сумма для вывода:</b> {min_withdrawal} ₽

📋 <b>Доступные способы выплаты:</b>
• 💳 Банковская карта
• 💎 ЮMoney
• 🏦 Сбербанк
• 🏪 Тинькофф

💡 <b>Выберите способ выплаты для создания запроса</b>
"""
                                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                                    [InlineKeyboardButton(text="💳 Банковская карта", callback_data="withdrawal_bank_card")],
                                                    [InlineKeyboardButton(text="💎 ЮMoney", callback_data="withdrawal_yoomoney")],
                                                    [InlineKeyboardButton(text="🏦 Сбербанк", callback_data="withdrawal_sberbank")],
                                                    [InlineKeyboardButton(text="🏪 Тинькофф", callback_data="withdrawal_tinkoff")],
                                                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")]
                                                ])
                                        else:
                                            text = "❌ Ошибка получения данных о выводах"
                                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                            ])
                                    else:
                                        text = "❌ Ошибка сервера"
                                        kb = InlineKeyboardMarkup(inline_keyboard=[
                                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                                        ])
                        else:
                            text = "❌ Ошибка получения баланса"
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                            ])
                    else:
                        text = "❌ Ошибка сервера"
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                        ])
        except Exception as e:
            print(f"Ошибка получения реферального баланса: {e}")
            text = "❌ Ошибка загрузки данных"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ])
    else:
        text = "❌ Система недоступна"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])
    
    await send_or_edit_message_with_photo(callback, text, reply_markup=kb, edit_message=True, image_name="money.png")


# Обработчики выбора способа выплаты
@router.callback_query(F.data.startswith("withdrawal_"))
async def withdrawal_method_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора способа выплаты"""
    user_id = callback.from_user.id
    method = callback.data.replace("withdrawal_", "")
    
    method_names = {
        "bank_card": "Банковская карта",
        "yoomoney": "ЮMoney",
        "sberbank": "Сбербанк",
        "tinkoff": "Тинькофф"
    }
    
    method_name = method_names.get(method, method)
    
    # Сохраняем выбранный способ в состоянии
    await state.update_data(withdrawal_method=method)
    
    text = f"""
💰 <b>Запрос на вывод средств</b>

💳 <b>Способ выплаты:</b> {method_name}

📝 <b>Введите реквизиты для выплаты:</b>

"""
    
    if method == "bank_card":
        text += """
💳 <b>Для банковской карты укажите:</b>
• Номер карты (16 цифр)
• Имя владельца карты

Пример: 1234 5678 9012 3456, Иван Иванов
"""
    elif method == "yoomoney":
        text += """
💎 <b>Для ЮMoney укажите:</b>
• Номер кошелька

Пример: 4100112345678901
"""
    elif method == "sberbank":
        text += """
🏦 <b>Для Сбербанка укажите:</b>
• Номер карты или счета

Пример: 1234 5678 9012 3456
"""
    elif method == "tinkoff":
        text += """
🏪 <b>Для Тинькофф укажите:</b>
• Номер карты

Пример: 1234 5678 9012 3456
"""
    
    text += """

💡 <b>Введите реквизиты в следующем сообщении</b>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="referral_withdrawal")]
    ])
    
    await send_or_edit_message_with_photo(callback, text, reply_markup=kb, edit_message=True, image_name="payment.png")
    
    # Устанавливаем состояние ожидания реквизитов
    await state.set_state(WithdrawalState.waiting_payment_details)


class WithdrawalState(StatesGroup):
    waiting_payment_details = State()
    waiting_amount = State()


# Обработчик поддержки
@router.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки поддержки"""
    support_text = """
🛠 <b>Техническая поддержка</b>

💬 <b>Свяжитесь с нашим менеджером:</b>

📱 Напишите нам в личные сообщения для решения любых вопросов:
• Проблемы с оплатой
• Вопросы по ключам
• Технические неполадки
• Другие вопросы

<i>Мы ответим вам в ближайшее время! ⚡</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, support_text, reply_markup=kb, edit_message=True, image_name="support.png")

# Обработчик отмены поддержки отключен - поддержка теперь через прямую ссылку
# @router.callback_query(F.data == "cancel_support")
# async def cancel_support(callback: CallbackQuery, state: FSMContext):
#     """Отмена обращения в поддержку"""
#     await state.clear()
#     
#     await send_or_edit_message_with_photo(callback, """
# ❌ <b>Обращение в поддержку отменено</b>
# 
# 💡 <b>Если у вас есть вопросы:</b>
# • Нажмите "🛠 Поддержка" снова
# • Или выберите другое действие
# 
# <i>Мы всегда готовы помочь! 🚀</i>
# """, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
#     [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")],
#     [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
# ]), edit_message=True, image_name="support.png")

# Обработчик ответа поддержке отключен - поддержка теперь через прямую ссылку
# @router.callback_query(F.data == "reply_to_support")
# async def reply_to_support_handler(callback: CallbackQuery, state: FSMContext):
#     """Обработчик кнопки 'Ответить поддержке'"""
#     user_id = callback.from_user.id
#     
#     # Получаем или создаем чат поддержки
#     chat_id = get_or_create_support_chat(user_id)
#     
#     # Устанавливаем состояние разрешения на одно сообщение
#     await state.set_state(SupportState.reply_allowed)
#     
#     # Отправляем сообщение с инструкцией
#     await send_or_edit_message_with_photo(callback, """
# 💬 <b>Вы можете ответить поддержке</b>
# 
# 📝 <b>Напишите ваше сообщение:</b>
# • Можете задать уточняющий вопрос
# • Приложить дополнительную информацию
# • Отправить скриншот
# 
# 📸 <b>Можете приложить фото</b> для лучшего понимания
# 
# <i>Напишите сообщение ниже ⬇️</i>
# """, edit_message=False, image_name="support.png")
#     
#     # Подтверждаем нажатие кнопки
#     await callback.answer("✅ Теперь вы можете ответить поддержке!")

# Обработчики поддержки отключены - поддержка теперь через прямую ссылку на менеджера
# Обработчик текстовых сообщений в поддержку
# @router.message(SupportState.waiting_for_message)
# async def handle_support_message(message: Message, state: FSMContext):
#     """Обработка сообщений пользователя в поддержку"""
#     user_id = message.from_user.id
#     
#     # Получаем или создаем чат поддержки
#     chat_id = get_or_create_support_chat(user_id)
#     
#     # Сохраняем сообщение в базу
#     with get_db() as conn:
#         conn.execute("""
#             INSERT INTO support_messages (chat_id, sender, text, sent_at, is_read)
#             VALUES (?, 'user', ?, CURRENT_TIMESTAMP, 0)
#         """, (chat_id, message.text))
#         
#         # Обновляем счетчик сообщений пользователя
#         conn.execute("""
#             UPDATE support_chats 
#             SET unread_user_messages = unread_user_messages + 1
#             WHERE chat_id = ?
#         """, (chat_id,))
#     
#     # Уведомляем только в группу поддержки (не в личные сообщения админам)
#     if SUPPORT_GROUP_ID:
#         try:
#             user_info = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name} {message.from_user.last_name}" or f"ID{user_id}"
#             
#             await bot.send_message(
#                 SUPPORT_GROUP_ID,
#                 f"""
# 🚨 <b>Новое сообщение в поддержке!</b>
# 
# 👤 <b>Пользователь:</b> {user_info}
# 🆔 <b>ID чата:</b> {chat_id}
# 📝 <b>Сообщение:</b> {message.text}
# 
# <i>Ответьте пользователю через админ панель</i>
# """,
#                 parse_mode="HTML"
#             )
#         except Exception as e:
#             logging.error(f"Ошибка уведомления в группу поддержки: {e}")
#     
#     # Подтверждаем получение сообщения
#     await send_or_edit_message_with_photo(message, """
# ✅ <b>Сообщение отправлено в поддержку!</b>
# 
# ⏰ <b>Время ответа:</b> обычно в течение 15-30 минут
# 
# 💡 <b>Можете:</b>
# • Отправить дополнительную информацию
# • Приложить скриншоты
# • Задать уточняющие вопросы
# 
# <i>Мы скоро ответим! 🚀</i>
# """, parse_mode="HTML", edit_message=False, image_name="support.png")

# Обработчик фотографий в поддержку отключен
# @router.message(SupportState.waiting_for_message, F.photo)
# async def handle_support_photo(message: Message, state: FSMContext):
#     """Обработка фотографий от пользователя в поддержку"""
#     user_id = message.from_user.id
#     
#     # Получаем или создаем чат поддержки
#     chat_id = get_or_create_support_chat(user_id)
#     
#     # Получаем лучшее качество фото
#     photo = message.photo[-1]
#     
#     # Сохраняем сообщение с фото в базу
#     with get_db() as conn:
#         conn.execute("""
#             INSERT INTO support_messages (chat_id, sender, text, photo_file_id, sent_at, is_read)
#             VALUES (?, 'user', ?, ?, CURRENT_TIMESTAMP, 0)
#         """, (chat_id, message.caption or "📸 Фото", photo.file_id))
#         
#         # Обновляем счетчик сообщений пользователя
#         conn.execute("""
#             UPDATE support_chats 
#             SET unread_user_messages = unread_user_messages + 1
#             WHERE chat_id = ?
#         """, (chat_id,))
#     
#     # Уведомляем только в группу поддержки (не в личные сообщения админам)
#     if SUPPORT_GROUP_ID:
#         try:
#             user_info = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name} {message.from_user.last_name}" or f"ID{user_id}"
#             
#             await bot.send_photo(
#                 SUPPORT_GROUP_ID,
#                 photo=photo.file_id,
#                 caption=f"""
# 📸 <b>Фото от пользователя в поддержку</b>
# 
# 👤 <b>Пользователь:</b> {user_info}
# 🆔 <b>ID чата:</b> {chat_id}
# 📝 <b>Подпись:</b> {message.caption or 'Без подписи'}
# 
# <i>Ответьте пользователю через админ панель</i>
# """,
#                 parse_mode="HTML"
#             )
#         except Exception as e:
#             logging.error(f"Ошибка уведомления в группу поддержки: {e}")
#     
#     # Подтверждаем получение фото
#     await send_or_edit_message_with_photo(message, """
# ✅ <b>Фото отправлено в поддержку!</b>
# 
# ⏰ <b>Время ответа:</b> обычно в течение 15-30 минут
# 
# 💡 <b>Можете:</b>
# • Отправить дополнительную информацию
# • Приложить еще фото
# • Задать уточняющие вопросы
# 
# <i>Мы скоро ответим! 🚀</i>
# """, parse_mode="HTML", edit_message=False, image_name="support.png")

# Обработчик обычных текстовых сообщений отключен
# @router.message(SupportState.waiting_for_message)
# @protect_message('support')
# async def handle_support_text(message: Message, state: FSMContext):
#     """Обработка текстовых сообщений в поддержку"""
#     await handle_support_message(message, state)

# Обработчик сообщений в состоянии reply_allowed отключен
# @router.message(SupportState.reply_allowed)
# @protect_message('support')
# async def handle_reply_allowed_message(message: Message, state: FSMContext):
#     """Обработка одного сообщения после нажатия кнопки 'Ответить поддержке'"""
#     user_id = message.from_user.id
#     
#     # Получаем или создаем чат поддержки
#     chat_id = get_or_create_support_chat(user_id)
#     
#     if message.photo:
#         # Обрабатываем фото
#         photo = message.photo[-1]
#         
#         # Сохраняем сообщение с фото в базу (БЕЗОПАСНО)
#         await save_support_message_safe(chat_id, 'user', message.caption or "📸 Фото", photo.file_id)
#         
#         # Уведомляем только в группу поддержки
#         if SUPPORT_GROUP_ID:
#             try:
#                 user_info = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name} {message.from_user.last_name}" or f"ID{user_id}"
#                 
#                 await bot.send_photo(
#                     SUPPORT_GROUP_ID,
#                     photo=photo.file_id,
#                     caption=f"""
# 📸 <b>Фото от пользователя в поддержку</b>
# 
# 👤 <b>Пользователь:</b> {user_info}
# 🆔 <b>ID чата:</b> {chat_id}
# 📝 <b>Подпись:</b> {message.caption or 'Без подписи'}
# 
# <i>Ответьте пользователю через админ панель</i>
# """,
#                     parse_mode="HTML"
#                 )
#             except Exception as e:
#                 logging.error(f"Ошибка уведомления в группу поддержки: {e}")
#         
#         # Подтверждаем получение фото
#         await send_or_edit_message_with_photo(message, """
# ✅ <b>Фото отправлено в поддержку!</b>
# 
# ⏰ <b>Время ответа:</b> обычно в течение 15-30 минут
# 
# 💡 <b>Для продолжения общения:</b>
# • Дождитесь ответа от поддержки
# • Нажмите кнопку "💬 Ответить поддержке" под следующим ответом
# 
# <i>Спасибо за обращение! 🚀</i>
# """, edit_message=False, image_name="support.png")
#         
#     else:
#         # Обрабатываем текстовое сообщение
#         # Сохраняем сообщение в базу (БЕЗОПАСНО)
#         await save_support_message_safe(chat_id, 'user', message.text)
#         
#         # Уведомляем только в группу поддержки
#         if SUPPORT_GROUP_ID:
#             try:
#                 user_info = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name} {message.from_user.last_name}" or f"ID{user_id}"
#                 
#                 await bot.send_message(
#                     SUPPORT_GROUP_ID,
#                     f"""
# 💬 <b>Новое сообщение в поддержку</b>
# 
# 👤 <b>Пользователь:</b> {user_info}
# 🆔 <b>ID чата:</b> {chat_id}
# 📝 <b>Сообщение:</b> {message.text}
# 
# <i>Ответьте пользователю через админ панель</i>
# """,
#                     parse_mode="HTML"
#                 )
#             except Exception as e:
#                 logging.error(f"Ошибка уведомления в группу поддержки: {e}")
#         
#         # Подтверждаем получение сообщения
#         await send_or_edit_message_with_photo(message, """
# ✅ <b>Сообщение отправлено в поддержку!</b>
# 
# ⏰ <b>Время ответа:</b> обычно в течение 15-30 минут
# 
# 💡 <b>Для продолжения общения:</b>
# • Дождитесь ответа от поддержки
# • Нажмите кнопку "💬 Ответить поддержке" под следующим ответом
# 
# <i>Спасибо за обращение! 🚀</i>
# """, parse_mode="HTML", edit_message=False, image_name="support.png")
#     
#     # Сбрасываем состояние - больше нельзя отправлять сообщения
#     await state.clear()

# Обработчик фотографий в состоянии reply_allowed отключен
# @router.message(SupportState.reply_allowed, F.photo)
# async def handle_reply_allowed_photo(message: Message, state: FSMContext):
#     """Обработка фотографий в состоянии reply_allowed"""
#     await handle_reply_allowed_message(message, state)

# Обработчик фотографий в состоянии waiting_for_message отключен
# @router.message(SupportState.waiting_for_message, F.photo)
# async def handle_waiting_photo(message: Message, state: FSMContext):
#     """Обработка фотографий в состоянии waiting_for_message"""
#     await handle_support_photo(message, state)

# Обработчик для реквизитов вывода средств
@router.message(WithdrawalState.waiting_payment_details)
async def handle_withdrawal_details(message: Message, state: FSMContext):
    """Обработка реквизитов для вывода средств"""
    user_id = message.from_user.id
    payment_details = message.text.strip()
    
    if not payment_details:
        await message.answer("❌ Пожалуйста, введите реквизиты для выплаты")
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    withdrawal_method = data.get('withdrawal_method')
    
    if not withdrawal_method:
        await message.answer("❌ Ошибка: способ выплаты не выбран")
        await state.clear()
        return
    
    # Запрашиваем сумму для вывода
    method_names = {
        "bank_card": "Банковская карта",
        "yoomoney": "ЮMoney",
        "sberbank": "Сбербанк",
        "tinkoff": "Тинькофф"
    }
    
    method_name = method_names.get(withdrawal_method, withdrawal_method)
    
    # Сохраняем реквизиты
    await state.update_data(payment_details=payment_details)
    
    # Получаем реферальный баланс пользователя
    referral_balance = 0
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/balance/{user_id}/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('success'):
                            referral_balance = data.get('referral_balance', 0)
        except Exception as e:
            print(f"Ошибка получения реферального баланса: {e}")
    
    text = f"""
💰 <b>Подтверждение запроса на вывод</b>

💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details}

💵 <b>Введите сумму для вывода (минимум 500 ₽):</b>

💰 <b>Ваш реферальный баланс:</b> {referral_balance} ₽
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="referral_withdrawal")]
    ])
    
    await message.answer(text, reply_markup=kb)
    
    # Переходим к состоянию ожидания суммы
    await state.set_state(WithdrawalState.waiting_amount)


@router.message(WithdrawalState.waiting_amount)
async def handle_withdrawal_amount(message: Message, state: FSMContext):
    """Обработка суммы для вывода средств"""
    user_id = message.from_user.id
    
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректную сумму (например: 500)")
        return
    
    # Проверяем минимальную сумму
    min_withdrawal = 500
    if amount < min_withdrawal:
        await message.answer(f"❌ Минимальная сумма для вывода: {min_withdrawal} ₽")
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    withdrawal_method = data.get('withdrawal_method')
    payment_details = data.get('payment_details')
    
    if not withdrawal_method or not payment_details:
        await message.answer("❌ Ошибка: данные не сохранены")
        await state.clear()
        return
    
    method_names = {
        "bank_card": "Банковская карта",
        "yoomoney": "ЮMoney",
        "sberbank": "Сбербанк",
        "tinkoff": "Тинькофф"
    }
    
    method_name = method_names.get(withdrawal_method, withdrawal_method)
    
    # Отправляем запрос на вывод
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/referral/withdrawal/request/'
            
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json={
                    'user_id': user_id,
                    'amount': amount,
                    'payment_method': withdrawal_method,
                    'payment_details': payment_details
                }) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get('success'):
                            text = f"""
✅ <b>Запрос на вывод создан!</b>

💰 <b>Сумма:</b> {amount} ₽
💳 <b>Способ выплаты:</b> {method_name}
📝 <b>Реквизиты:</b> {payment_details}

⏳ <b>Ваш запрос отправлен на рассмотрение администратору</b>

📋 <b>Статус:</b> В ожидании
⏰ <b>Время обработки:</b> до 24 часов

💡 <b>Вы получите уведомление об изменении статуса</b>
"""
                            
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📋 Проверить статус", callback_data="referral_withdrawal")],
                                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
                            ])
                            
                            await message.answer(text, reply_markup=kb)
                            
                            # Отправляем уведомление о новой заявке
                            withdrawal_data = {
                                'user_id': user_id,
                                'amount': amount,
                                'payment_method': withdrawal_method,
                                'payment_details': payment_details,
                                'username': message.from_user.username,
                                'first_name': message.from_user.first_name
                            }
                            await send_withdrawal_notification('new_request', withdrawal_data)
                        else:
                            error_message = data.get('message', 'Неизвестная ошибка')
                            await message.answer(f"❌ Ошибка создания запроса: {error_message}")
                    else:
                        await message.answer("❌ Ошибка сервера при создании запроса")
        except Exception as e:
            print(f"Ошибка создания запроса на вывод: {e}")
            await message.answer("❌ Ошибка создания запроса")
    else:
        await message.answer("❌ Система недоступна")
    
    # Очищаем состояние
    await state.clear()



# Универсальный обработчик для неизвестных команд
@router.message()
async def handle_unknown_message(message: Message, state: FSMContext):
    """Обработчик для всех неизвестных сообщений"""
    # Пропускаем сообщения, если пользователь находится в состоянии рассылки или загрузки ключей
    current_state = await state.get_state()
    if current_state and current_state.startswith("AdminPromoState:"):
        if current_state == "AdminPromoState:waiting_code":
            await admin_promo_code_input(message, state)
        elif current_state == "AdminPromoState:waiting_discount":
            await admin_promo_discount_input(message, state)
        elif current_state == "AdminPromoState:waiting_max_per_user":
            await admin_promo_max_per_user_input(message, state)
        elif current_state == "AdminPromoState:waiting_max_uses":
            await admin_promo_max_uses_input(message, state)
        return
    if current_state and current_state.startswith("PromoState:"):
        if current_state == "PromoState:waiting_code":
            await promo_code_input_handler(message, state)
        return
    if current_state and (current_state.startswith("BroadcastState:") or current_state.startswith("UploadKeysState:")
                          or current_state.startswith("WithdrawalState:") or current_state.startswith("AdminState:")):
        return
    
    # Пропускаем команды (/ban и др.)
    if message.text and message.text.startswith('/'):
        return
    
    await message.answer("🤖 Я не знаю такой команды. Используйте /start для доступа к главному меню.")


# --- Админ-панель обработчики ---

@router.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    """Обработчик статистики для админки"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    users = data.get('users', {})
                    payments = data.get('payments', {})
                    keys = data.get('keys', {})
                    
                    stats_text = f"""
📊 <b>Статистика системы</b>

👥 <b>Пользователи:</b>
• Всего: <b>{users.get('total', 0)}</b>
• Новых сегодня: <b>{users.get('new_today', 0)}</b>
• Новых за неделю: <b>{users.get('new_week', 0)}</b>

💳 <b>Платежи:</b>
• Всего: <b>{payments.get('total', 0)}</b>
• Ожидают обработки: <b>{payments.get('pending', 0)}</b>
• Успешных: <b>{payments.get('succeeded', 0)}</b>
• Выручка: <b>{payments.get('revenue', 0):.2f} ₽</b>
• <b>За сегодня:</b> {payments.get('revenue_today', 0):.2f} ₽

🔑 <b>Ключи:</b>
• Всего ключей: <b>{keys.get('total', 0)}</b>
• Доступно: <b>{keys.get('available', 0)}</b>
"""
                else:
                    stats_text = "❌ <b>Ошибка загрузки статистики</b>"
    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        stats_text = "❌ <b>Ошибка загрузки данных</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])
    
    try:
        await callback.message.edit_text(stats_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(stats_text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: CallbackQuery):
    """Обработчик главного меню админки"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            
            api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        users = data.get('users', {})
                        payments = data.get('payments', {})
                        keys = data.get('keys', {})
                        
                        admin_text = f"""
🔧 <b>Панель администратора</b>

📊 <b>Статистика:</b>

👥 <b>Пользователи:</b>
• Всего: {users.get('total', 0)}
• Сегодня: {users.get('new_today', 0)}
• За неделю: {users.get('new_week', 0)}

💳 <b>Платежи:</b>
• Всего: {payments.get('total', 0)}
• Ожидают: {payments.get('pending', 0)}
• Успешных: {payments.get('succeeded', 0)}
• Выручка: {payments.get('revenue', 0):.2f} ₽
• <b>За сегодня:</b> {payments.get('revenue_today', 0):.2f} ₽

🔑 <b>Ключи:</b>
• Всего: {keys.get('total', 0)}
• Доступно: {keys.get('available', 0)}

<i>Выберите раздел для управления ⬇️</i>
"""
                    else:
                        admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Ошибка загрузки статистики</b>

<i>Попробуйте позже</i>
"""
        except Exception as e:
            logging.error(f"Ошибка получения статистики для админки: {e}")
            admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Ошибка загрузки данных</b>

<i>Попробуйте позже</i>
"""
    else:
        admin_text = """
🔧 <b>Панель администратора</b>

❌ <b>Система недоступна</b>

<i>Django интеграция не настроена</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="admin_keys")],
        [InlineKeyboardButton(text="👥 Рефереры", callback_data="admin_referrers")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔔 Планировщик", callback_data="admin_scheduler")],
        [InlineKeyboardButton(text="🎫 Промокоды", callback_data="admin_promo")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    try:
        await callback.message.edit_text(admin_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(admin_text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_promo")
async def admin_promo_handler(callback: CallbackQuery):
    """Список промокодов"""
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await _show_promo_list(callback, page=0)


@router.callback_query(F.data.startswith("admin_promo_page_"))
async def admin_promo_page_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        return
    page = int(callback.data.split("_")[-1])
    await _show_promo_list(callback, page=page)


async def _show_promo_list(callback: CallbackQuery, page: int = 0):
    from asgiref.sync import sync_to_async
    from bot_management.models import PromoCode

    per_page = 8
    all_promos = await sync_to_async(list)(PromoCode.objects.all().order_by('-created_at'))
    total = len(all_promos)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    promos = all_promos[page * per_page:(page + 1) * per_page]

    lines = [f"🎫 <b>Промокоды</b> (всего: {total})\n"]
    kb_buttons = []
    for p in promos:
        status = "✅" if p.is_active else "❌"
        expiry = f" до {p.expires_at.strftime('%d.%m.%Y')}" if p.expires_at else ""
        lines.append(f"{status} <b>{p.code}</b> — -{p.discount_percent}%{expiry}")
        lines.append(f"   Использовано: {p.current_uses}/{p.max_uses if p.max_uses else '∞'}, на пользователя: {getattr(p, 'max_uses_per_user', 1) if getattr(p, 'max_uses_per_user', 1) else '∞'}")
        kb_buttons.append([InlineKeyboardButton(text=f"❌ {p.code}", callback_data=f"admin_promo_delete_{p.id}")])
    text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_promo_page_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_promo_page_{page + 1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo")],
        *kb_buttons,
        nav,
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promo_delete_"))
async def admin_promo_delete_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    promo_id = int(callback.data.split("_")[-1])
    from bot_management.models import PromoCode
    from asgiref.sync import sync_to_async
    promo = await sync_to_async(PromoCode.objects.get)(id=promo_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_promo_confirm_delete_{promo_id}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_promo")]
    ])
    await callback.message.edit_text(
        f"🗑 Удалить промокод <b>{promo.code}</b> (-{promo.discount_percent}%)?\n"
        f"Использован: {promo.current_uses} раз(а)",
        parse_mode="HTML", reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promo_confirm_delete_"))
async def admin_promo_confirm_delete_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    promo_id = int(callback.data.split("_")[-1])
    from bot_management.models import PromoCode
    from asgiref.sync import sync_to_async
    promo = await sync_to_async(PromoCode.objects.get)(id=promo_id)
    await sync_to_async(promo.delete)()
    await callback.answer(f"✅ Промокод {promo.code} удалён", show_alert=True)
    await _show_promo_list(callback, page=0)


@router.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await state.set_state(AdminPromoState.waiting_code)
    await callback.message.answer("🎫 Введите код промокода (латиница, цифры):")
    await callback.answer()


@router.message(AdminPromoState.waiting_code)
async def admin_promo_code_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = message.text.strip().upper()
    await state.update_data(code=code)
    await state.set_state(AdminPromoState.waiting_discount)
    await message.answer("📊 Введите размер скидки (процент, только число):")


@router.message(AdminPromoState.waiting_discount)
async def admin_promo_discount_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        discount = int(message.text.strip())
        if discount < 1 or discount > 100:
            await message.answer("❌ Скидка должна быть от 1 до 100")
            return
    except ValueError:
        await message.answer("❌ Введите число от 1 до 100")
        return
    await state.update_data(discount=discount)
    await state.set_state(AdminPromoState.waiting_max_per_user)
    await message.answer("👤 Введите макс. использований на <b>одного пользователя</b> (0 = безлимит, 1 = разово):")


@router.message(AdminPromoState.waiting_max_per_user)
async def admin_promo_max_per_user_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        max_per_user = int(message.text.strip())
        if max_per_user < 0:
            await message.answer("❌ Введите 0 или положительное число")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return
    await state.update_data(max_per_user=max_per_user)
    await state.set_state(AdminPromoState.waiting_max_uses)
    await message.answer("👥 Введите макс. количество использований всего (0 = безлимит):")


@router.message(AdminPromoState.waiting_max_uses)
async def admin_promo_max_uses_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        max_uses = int(message.text.strip())
        if max_uses < 0:
            await message.answer("❌ Введите 0 или положительное число")
            return
    except ValueError:
        await message.answer("❌ Введите число")
        return

    data = await state.get_data()
    from asgiref.sync import sync_to_async
    from bot_management.models import PromoCode

    try:
        promo = await sync_to_async(PromoCode.objects.create)(
            code=data['code'],
            discount_percent=data['discount'],
            max_uses=max_uses,
            max_uses_per_user=data.get('max_per_user', 1),
            is_active=True
        )
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"🎫 <b>{promo.code}</b>\n"
            f"📊 Скидка: -{promo.discount_percent}%\n"
            f"👥 Использований: {promo.current_uses}/{promo.max_uses if promo.max_uses else '∞'}\n"
            f"👤 На пользователя: {getattr(promo, 'max_uses_per_user', 1) if getattr(promo, 'max_uses_per_user', 1) else '∞'}",
            parse_mode="HTML"
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            await message.answer(f"❌ Код <b>{data['code']}</b> уже существует")
        else:
            await message.answer(f"❌ Ошибка: {e}")
    await state.clear()


@router.callback_query(F.data == "admin_users")
async def admin_users_handler(callback: CallbackQuery):
    """Обработчик списка пользователей"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    # Показываем первую страницу списка пользователей
    await show_users_list(callback, page=1)
    
    await callback.answer()

@router.callback_query(F.data == "admin_payments")
async def admin_payments_handler(callback: CallbackQuery):
    """Обработчик списка платежей"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    # Показываем первую страницу списка платежей
    await show_payments_list(callback, page=1)
    
    await callback.answer()

@router.callback_query(F.data == "admin_keys")
async def admin_keys_handler(callback: CallbackQuery):
    """Обработчик управления ключами"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    keys = data.get('keys', {})
                    
                    keys_text = f"""
🔑 <b>Управление ключами</b>

📊 <b>Статистика ключей:</b>
• Всего ключей: <b>{keys.get('total', 0)}</b>
• Доступно: <b>{keys.get('available', 0)}</b>

<i>Выберите действие ⬇️</i>
"""
                else:
                    keys_text = "❌ <b>Ошибка загрузки данных</b>"
    except Exception as e:
        logging.error(f"Ошибка получения ключей: {e}")
        keys_text = "❌ <b>Ошибка загрузки данных</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Загрузить ключи", callback_data="admin_upload_keys")],
        [InlineKeyboardButton(text="📋 Список ключей", callback_data="admin_keys_list")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_keys")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])
    
    try:
        await callback.message.edit_text(keys_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(keys_text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик рассылки"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    # Очищаем состояние при возврате в меню рассылки
    await state.clear()
    
    # Получаем количество пользователей
    user_count = 0
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            api_url = f'{DJANGO_API_URL}/bot_management/api/statistics/'
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        user_count = data.get('users', {}).get('total', 0)
        except Exception as e:
            logging.error(f"Ошибка получения статистики для рассылки: {e}")
    else:
        # Используем прямую базу данных
        with get_db() as conn:
            result = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()
            user_count = result['count'] if result else 0
    
    broadcast_text = f"""
📢 <b>Рассылка сообщений</b>

👥 <b>Получателей:</b> {user_count} пользователей

📝 <b>Инструкция:</b>
1. Нажмите "Начать рассылку"
2. Отправьте текст сообщения (можно с фото или видео)
3. (Необязательно) Добавьте строки для кнопок формата:
   <code>btn: Текст кнопки | https://example.com</code>
   или
   <code>btn: Текст кнопки | catalog</code>
4. Подтвердите отправку

⚠️ <b>Внимание:</b> Рассылка будет отправлена всем пользователям бота!
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Начать рассылку", callback_data="start_broadcast")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])
    
    try:
        await callback.message.edit_text(broadcast_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(broadcast_text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data == "start_broadcast")
async def start_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    """Начало создания рассылки"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    logging.info(f"DEBUG: Начало создания рассылки для админа {user_id}")
    
    await callback.message.answer(
        "📝 <b>Введите текст сообщения для рассылки:</b>\n\n"
        "💡 <i>Можно прикрепить фото или видео – они будут отправлены вместе с текстом.</i>\n"
        "💡 <i>Для добавления кнопок добавьте строки вида:</i>\n"
        "   <code>btn: Текст кнопки | https://example.com</code>\n"
        "   <code>btn: Текст кнопки | catalog</code>\n"
        "💡 <i>Для отмены введите /cancel</i>",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastState.waiting_message)
    current_state = await state.get_state()
    logging.info(f"DEBUG: Установлено состояние рассылки: {current_state}, ожидается: {BroadcastState.waiting_message}")
    await callback.answer()

@router.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и отправка рассылки"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    data = await state.get_data()
    message_text = data.get('message_text', '')
    user_count = data.get('user_count', 0)
    photo_file_id = data.get('photo_file_id')
    video_file_id = data.get('video_file_id')
    buttons = data.get('buttons', []) or []
    
    if not message_text:
        await callback.answer("❌ Ошибка: текст сообщения не найден", show_alert=True)
        await state.clear()
        return
    
    # Уведомляем о начале рассылки
    start_text = (
        f"📢 <b>Рассылка началась</b>\n\n"
        f"👥 Отправка {user_count} пользователям...\n"
        f"⏳ Пожалуйста, подождите..."
    )
    try:
        if callback.message.photo or callback.message.video:
            # Превью было с медиа – редактируем подпись
            await callback.message.edit_caption(start_text)
        else:
            await callback.message.edit_text(start_text)
    except Exception as e:
        logging.warning(f"Не удалось отредактировать сообщение при старте рассылки: {e}, отправляем новое")
        await callback.message.answer(start_text)
    
    await callback.answer("Рассылка началась...")
    
    # Собираем клавиатуру для рассылки (кнопки под постом)
    reply_keyboard = None
    if buttons:
        kb_rows = []
        for b in buttons:
            text = b.get('text')
            if not text:
                continue
            if 'url' in b:
                kb_rows.append([InlineKeyboardButton(text=text, url=b['url'])])
            elif 'callback_data' in b:
                kb_rows.append([InlineKeyboardButton(text=text, callback_data=b['callback_data'])])
        if kb_rows:
            reply_keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    # Получаем список пользователей
    users = []
    if DJANGO_INTEGRATION:
        try:
            import aiohttp
            # Получаем список пользователей через API или напрямую из базы
            # Пока используем прямую базу данных
            with get_db() as conn:
                users = conn.execute("SELECT user_id FROM users").fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения пользователей: {e}")
            users = []
    else:
        with get_db() as conn:
            users = conn.execute("SELECT user_id FROM users").fetchall()
    
    # Отправляем рассылку
    sent_count = 0
    failed_count = 0
    
    for user in users:
        try:
            user_id = user['user_id'] if isinstance(user, dict) else user[0]
            
            if video_file_id:
                await bot.send_video(
                    chat_id=user_id,
                    video=video_file_id,
                    caption=message_text,
                    parse_mode="HTML",
                    reply_markup=reply_keyboard
                )
            elif photo_file_id:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                    caption=message_text,
                    parse_mode="HTML",
                    reply_markup=reply_keyboard
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML",
                    reply_markup=reply_keyboard
                )
            
            sent_count += 1
            
            # Небольшая задержка, чтобы не превысить лимиты API
            if sent_count % 30 == 0:
                await asyncio.sleep(1)
                # Обновляем статус каждые 30 сообщений
                progress_text = (
                    f"📢 <b>Рассылка в процессе</b>\n\n"
                    f"✅ Отправлено: {sent_count}/{user_count}\n"
                    f"❌ Ошибок: {failed_count}\n"
                    f"⏳ Продолжается..."
                )
                try:
                    if callback.message.photo or callback.message.video:
                        await callback.message.edit_caption(progress_text)
                    else:
                        await callback.message.edit_text(progress_text)
                except Exception:
                    pass
            
        except Exception as e:
            failed_count += 1
            logging.error(f"Ошибка отправки пользователю {user_id}: {e}")
    
    # Финальный результат
    result_text = f"""
✅ <b>Рассылка завершена!</b>

📊 <b>Статистика:</b>
• ✅ Отправлено: {sent_count}
• ❌ Ошибок: {failed_count}
• 📊 Всего получателей: {user_count}
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])
    
    try:
        if callback.message.photo or callback.message.video:
            await callback.message.edit_caption(result_text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(result_text, parse_mode="HTML", reply_markup=kb)
    
    await state.clear()


@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await callback.answer()


# --- Обработчики загрузки ключей и настроек ---

@router.callback_query(F.data == "admin_upload_keys")
async def admin_upload_keys_handler(callback: CallbackQuery, state: FSMContext):
    """Начало загрузки ключей"""
    logging.info(f"DEBUG: Получен callback admin_upload_keys от {callback.from_user.id}")
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        logging.warning(f"DEBUG: Попытка доступа к загрузке ключей от не-админа {user_id}")
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    text = """
📥 <b>Загрузка ключей</b>

📝 <b>Инструкция:</b>
1. Выберите тип подписки
2. Выберите количество активаций
3. Отправьте список ключей (каждый ключ с новой строки или через запятую)

<i>Нажмите кнопку ниже для начала</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Пробная (3 дня)", callback_data="upload_keys_trial")],
        [InlineKeyboardButton(text="📅 Месячная (общая база для 1 мес / 3 мес / год)", callback_data="upload_keys_month")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_keys")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data.startswith("upload_keys_"))
async def admin_upload_keys_type_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор типа подписки для загрузки ключей"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    sub_type = callback.data.replace("upload_keys_", "")
    
    # Сохраняем тип подписки
    await state.update_data(subscription_type=sub_type)
    
    text = f"""
📥 <b>Загрузка ключей</b>

📅 <b>Тип подписки:</b> {sub_type}

📝 <b>Выберите количество активаций:</b>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data=f"upload_activations_{sub_type}_1"),
         InlineKeyboardButton(text="2", callback_data=f"upload_activations_{sub_type}_2"),
         InlineKeyboardButton(text="3", callback_data=f"upload_activations_{sub_type}_3"),
         InlineKeyboardButton(text="4", callback_data=f"upload_activations_{sub_type}_4"),
         InlineKeyboardButton(text="5", callback_data=f"upload_activations_{sub_type}_5")],
        [InlineKeyboardButton(text="6", callback_data=f"upload_activations_{sub_type}_6"),
         InlineKeyboardButton(text="7", callback_data=f"upload_activations_{sub_type}_7"),
         InlineKeyboardButton(text="8", callback_data=f"upload_activations_{sub_type}_8"),
         InlineKeyboardButton(text="9", callback_data=f"upload_activations_{sub_type}_9"),
         InlineKeyboardButton(text="10", callback_data=f"upload_activations_{sub_type}_10")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_upload_keys")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data.startswith("upload_activations_"))
async def admin_upload_keys_activations_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор количества активаций и запрос ключей"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    # Парсим данные из callback_data: upload_activations_{sub_type}_{activations}
    parts = callback.data.split("_")
    sub_type = parts[2]
    activations = int(parts[3])
    
    # Сохраняем данные
    await state.update_data(
        subscription_type=sub_type,
        total_activations=activations
    )
    
    text = f"""
📥 <b>Загрузка ключей</b>

📅 <b>Тип подписки:</b> {sub_type}
🔢 <b>Активаций:</b> {activations}

📝 <b>Отправьте список ключей:</b>
• Каждый ключ с новой строки
• Или через запятую

<i>Пример:</i>
<code>key1
key2
key3</code>

<i>Или:</i>
<code>key1, key2, key3</code>

<i>Для отмены отправьте /cancel</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_keys")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await state.set_state(UploadKeysState.waiting_keys)
    await callback.answer()

# --- Функции для отображения списков ---

async def show_keys_list(callback: CallbackQuery, page=1):
    """Отображение списка ключей"""
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/keys/list/?page={page}&limit=10'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    keys = data.get('keys', [])
                    total = data.get('total', 0)
                    pages = data.get('pages', 1)
                    
                    keys_text = f"""
🔑 <b>Список ключей</b>

📊 <b>Всего:</b> {total}
📄 <b>Страница:</b> {page}/{pages}

"""
                    keys_text += "\n<b>Нажмите на ключ для управления:</b>\n\n"
                    
                    kb_buttons = []
                    # Создаем кнопки для каждого ключа
                    for key in keys[:10]:  # Показываем первые 10
                        status = "✅" if key.get('is_active') else "❌"
                        sub_type = key.get('subscription_type', 'N/A')
                        key_value = key.get('key_value', 'N/A')
                        key_id = key.get('key_id')
                        
                        # Сокращаем ключ для отображения на кнопке
                        button_text = f"{status} {sub_type}"
                        if len(key_value) > 15:
                            button_text += f" - {key_value[:15]}..."
                        else:
                            button_text += f" - {key_value}"
                        
                        kb_buttons.append([InlineKeyboardButton(
                            text=button_text,
                            callback_data=f"admin_key_detail_{key_id}"
                        )])
                    
                    # Кнопки навигации
                    nav_buttons = []
                    if page > 1:
                        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_keys_list_page_{page-1}"))
                    if page < pages:
                        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_keys_list_page_{page+1}"))
                    if nav_buttons:
                        kb_buttons.append(nav_buttons)
                    kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад в управление ключами", callback_data="admin_keys")])
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                    
                    try:
                        await callback.message.edit_text(keys_text, parse_mode="HTML", reply_markup=kb)
                    except:
                        await callback.message.answer(keys_text, parse_mode="HTML", reply_markup=kb)
                else:
                    await callback.message.answer("❌ Ошибка загрузки списка ключей")
    except Exception as e:
        logging.error(f"Ошибка получения списка ключей: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


async def show_payments_list(callback: CallbackQuery, page=1):
    """Отображение списка платежей"""
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/payments/list/?page={page}&limit=10'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    payments = data.get('payments', [])
                    total = data.get('total', 0)
                    pages = data.get('pages', 1)
                    
                    payments_text = f"""
💳 <b>Список платежей</b>

📊 <b>Всего:</b> {total}
📄 <b>Страница:</b> {page}/{pages}

<b>Нажмите на платеж для управления:</b>

"""
                    
                    kb_buttons = []
                    # Создаем кнопки для каждого платежа
                    for payment in payments[:10]:  # Показываем первые 10
                        payment_id = payment.get('payment_id', 'N/A')
                        amount = payment.get('amount', 0)
                        status = payment.get('status', 'N/A')
                        sub_type = payment.get('subscription_type', 'N/A')
                        
                        status_emoji = {
                            'pending': '⏳',
                            'succeeded': '✅',
                            'canceled': '❌',
                            'failed': '❌'
                        }.get(status, '❓')
                        
                        button_text = f"{status_emoji} #{payment_id} - {amount} ₽ ({sub_type})"
                        kb_buttons.append([InlineKeyboardButton(
                            text=button_text,
                            callback_data=f"admin_payment_detail_{payment_id}"
                        )])
                    
                    # Кнопки навигации
                    nav_buttons = []
                    if page > 1:
                        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_payments_list_page_{page-1}"))
                    if page < pages:
                        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_payments_list_page_{page+1}"))
                    if nav_buttons:
                        kb_buttons.append(nav_buttons)
                    kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")])
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                    
                    try:
                        await callback.message.edit_text(payments_text, parse_mode="HTML", reply_markup=kb)
                    except:
                        await callback.message.answer(payments_text, parse_mode="HTML", reply_markup=kb)
                else:
                    await callback.message.answer("❌ Ошибка загрузки списка платежей")
    except Exception as e:
        logging.error(f"Ошибка получения списка платежей: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


async def show_users_list(callback: CallbackQuery, page=1):
    """Отображение списка пользователей"""
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/users/list/?page={page}&limit=10'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    users = data.get('users', [])
                    total = data.get('total', 0)
                    pages = data.get('pages', 1)
                    
                    users_text = f"""
👥 <b>Список пользователей</b>

📊 <b>Всего:</b> {total}
📄 <b>Страница:</b> {page}/{pages}

"""
                    for user in users[:10]:  # Показываем первые 10
                        user_id = user.get('user_id', 'N/A')
                        username = user.get('username', 'N/A') or user.get('first_name', 'N/A')
                        first_name = user.get('first_name', '')
                        last_name = user.get('last_name', '')
                        name = f"{first_name} {last_name}".strip() or username
                        
                        users_text += f"👤 <b>{name}</b>\n"
                        users_text += f"   ID: {user_id} | @{username if username != 'N/A' else 'нет'}\n\n"
                    
                    kb_buttons = []
                    if page > 1:
                        kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_users_list_page_{page-1}")])
                    if page < pages:
                        kb_buttons.append([InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_users_list_page_{page+1}")])
                    kb_buttons.append([
                        InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_find_user"),
                        InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")
                    ])
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                    
                    try:
                        await callback.message.edit_text(users_text, parse_mode="HTML", reply_markup=kb)
                    except:
                        await callback.message.answer(users_text, parse_mode="HTML", reply_markup=kb)
                else:
                    await callback.message.answer("❌ Ошибка загрузки списка пользователей")
    except Exception as e:
        logging.error(f"Ошибка получения списка пользователей: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


def _referrers_list_callback_data(page: int, search_query: str = "") -> str:
    """Формирует callback_data для пагинации (до 64 символов). При поиске сохраняем запрос."""
    base = f"admin_referrers_list_page_{page}"
    if search_query:
        # Ограничиваем длину поиска для лимита Telegram callback_data (64 байта)
        search_short = search_query.strip()[:28]
        return f"{base}_s_{search_short}"
    return base


async def show_referrers_list(callback: CallbackQuery, page=1, search_query=""):
    """Отображение списка рефереров"""
    try:
        import aiohttp
        import urllib.parse

        api_url = f'{DJANGO_API_URL}/bot_management/api/referrers/list/?page={page}&limit=10'
        if search_query:
            api_url += f'&search={urllib.parse.quote(search_query.strip())}'

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status != 200:
                        text = await response.text()
                        logging.error(f"API referrers list: status {response.status}, body: {text[:200]}")
                        await callback.message.answer(
                            "❌ Ошибка загрузки списка рефереров. Проверьте, что Django-сервер запущен (порт 8123)."
                        )
                        return

                    data = await response.json()
                    if not data.get('success', True):
                        await callback.message.answer(f"❌ {data.get('message', 'Ошибка API')}")
                        return

                    referrers = data.get('referrers', [])
                    total = data.get('total', 0)
                    pages = data.get('pages', 1) or 1
                    search = data.get('search', '')

                    referrers_text = "👥 <b>Список рефереров</b>\n\n"
                    referrers_text += f"📊 <b>Всего:</b> {total}\n"
                    referrers_text += f"📄 <b>Страница:</b> {page}/{pages}\n"
                    if search:
                        referrers_text += f"🔍 <b>Поиск:</b> {search}\n"
                    referrers_text += "\n<b>Нажмите на реферера для просмотра статистики:</b>\n\n"

                    kb = None
                    if total == 0:
                        referrers_text += "Ничего не найдено. Попробуйте другой запрос (username без @, имя, ID)."
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_referrers_search")],
                            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
                        ])
                    else:
                        kb_buttons = []
                        for referrer in referrers[:10]:
                            user_id = referrer.get('user_id', 'N/A')
                            username = referrer.get('username') or referrer.get('first_name') or 'N/A'
                            referrals_count = referrer.get('referrals_count', 0)
                            purchases_count = referrer.get('purchases_count', 0)
                            has_purchases = referrer.get('has_purchases', False)
                            purchase_status = "[Есть покупки]" if has_purchases else "[Нет покупок]"
                            button_text = f"{username} (ID: {user_id}) {purchase_status}\n   Рефералов: {referrals_count}"
                            if purchases_count > 0:
                                button_text += f" | Покупок: {purchases_count}"
                            if len(button_text) > 60:
                                button_text = button_text[:57] + "..."
                            kb_buttons.append([InlineKeyboardButton(
                                text=button_text,
                                callback_data=f"admin_referrer_detail_{user_id}"
                            )])

                        nav_buttons = []
                        if page > 1:
                            nav_buttons.append(InlineKeyboardButton(
                                text="⬅️ Назад",
                                callback_data=_referrers_list_callback_data(page - 1, search_query)
                            ))
                        if page < pages:
                            nav_buttons.append(InlineKeyboardButton(
                                text="Вперед ➡️",
                                callback_data=_referrers_list_callback_data(page + 1, search_query)
                            ))
                        if nav_buttons:
                            kb_buttons.append(nav_buttons)

                        kb_buttons.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_referrers_search")])
                        kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")])
                        kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

                    # Если пришли из поиска (текстовое сообщение) — всегда answer; иначе пробуем edit
                    from_search = bool(search_query)
                    try:
                        if from_search:
                            await callback.message.answer(referrers_text, parse_mode="HTML", reply_markup=kb)
                        else:
                            await callback.message.edit_text(referrers_text, parse_mode="HTML", reply_markup=kb)
                    except Exception:
                        await callback.message.answer(referrers_text, parse_mode="HTML", reply_markup=kb)

            except asyncio.TimeoutError:
                logging.error("Timeout при запросе к API рефереров")
                await callback.message.answer("❌ Превышено время ожидания ответа от сервера. Попробуйте позже.")
            except aiohttp.ClientError as e:
                logging.error(f"Ошибка соединения с API рефереров: {e}")
                await callback.message.answer("❌ Не удалось подключиться к серверу. Убедитесь, что Django запущен (порт 8123).")

    except Exception as e:
        logging.error(f"Ошибка получения списка рефереров: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


# --- Обработчики для списков ---

@router.callback_query(F.data.startswith("admin_keys_list"))
async def admin_keys_list_handler(callback: CallbackQuery):
    """Обработчик списка ключей с пагинацией"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if callback.data == "admin_keys_list":
        page = 1
    elif callback.data.startswith("admin_keys_list_page_"):
        page = int(callback.data.split("_")[-1])
    else:
        page = 1
    
    await show_keys_list(callback, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_payments_list_page_"))
async def admin_payments_list_handler(callback: CallbackQuery):
    """Обработчик списка платежей с пагинацией"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    page = int(callback.data.split("_")[-1])
    await show_payments_list(callback, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_users_list_page_"))
async def admin_users_list_handler(callback: CallbackQuery):
    """Обработчик списка пользователей с пагинацией"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    page = int(callback.data.split("_")[-1])
    await show_users_list(callback, page=page)
    await callback.answer()


@router.callback_query(F.data == "admin_referrers")
async def admin_referrers_handler(callback: CallbackQuery):
    """Обработчик списка рефереров"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    # Показываем первую страницу списка рефереров
    await show_referrers_list(callback, page=1)
    
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referrers_list_page_"))
async def admin_referrers_list_handler(callback: CallbackQuery):
    """Обработчик списка рефереров с пагинацией (поддержка поиска в callback_data)"""
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    # Формат: admin_referrers_list_page_2 или admin_referrers_list_page_2_s_запрос
    parts = callback.data.split("_")
    try:
        page_idx = parts.index("page")
        page = int(parts[page_idx + 1])
    except (ValueError, IndexError):
        page = 1
    search_query = ""
    if "_s_" in callback.data:
        try:
            # после "page_N_s_" идёт поисковый запрос
            search_query = callback.data.split("_s_", 1)[1].strip()
        except IndexError:
            pass

    await show_referrers_list(callback, page=page, search_query=search_query)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referrer_detail_"))
async def admin_referrer_detail_handler(callback: CallbackQuery):
    """Обработчик детальной статистики реферала"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        referrer_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/referrers/{referrer_id}/detail/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success'):
                        referrer = data.get('referrer', {})
                        stats = data.get('stats', {})
                        referrals = data.get('referrals', [])
                        
                        referrer_name = referrer.get('username') or referrer.get('first_name') or f"ID{referrer.get('user_id')}"
                        referral_code = referrer.get('referral_code', 'Не создан')
                        
                        referrals_with_purchases = stats.get('referrals_with_purchases_count', 0)
                        total_referrals = stats.get('referrals_count', 0)
                        
                        detail_text = f"""
👥 <b>Статистика реферала</b>

👤 <b>Реферер:</b> {referrer_name}
🆔 <b>ID:</b> {referrer.get('user_id')}
🔑 <b>Реферальный код:</b> {referral_code}
💰 <b>Реферальный баланс:</b> {referrer.get('referral_balance', 0):.2f} ₽

📊 <b>Общая статистика:</b>
• Всего рефералов: <b>{total_referrals}</b>
• Рефералов с покупками: <b>{referrals_with_purchases}</b>
• Покупок: <b>{stats.get('total_purchases', 0)}</b>
• Выручка: <b>{stats.get('total_revenue', 0):.2f} ₽</b>
• Комиссия: <b>{stats.get('total_commission', 0):.2f} ₽</b> ({stats.get('commission_percent', 20)}%)

📋 <b>Рефералы, которые перешли по ссылке и купили:</b>
"""
                        # Показываем рефералов (уже отфильтрованы - только с покупками)
                        if not referrals:
                            detail_text += "\n❌ <i>Нет рефералов с покупками</i>"
                        else:
                            for i, ref in enumerate(referrals[:20], 1):
                                ref_name = ref.get('username') or ref.get('first_name') or f"ID{ref.get('user_id')}"
                                purchases_count = ref.get('purchases_count', 0)
                                total_spent = ref.get('total_spent', 0)
                                
                                detail_text += f"\n{i}. <b>{ref_name}</b> (ID: {ref.get('user_id')})"
                                detail_text += f"\n   Покупок: {purchases_count} | Потрачено: {total_spent:.2f} ₽"
                            
                            if len(referrals) > 20:
                                detail_text += f"\n\n... и еще {len(referrals) - 20} рефералов с покупками"
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📥 Экспорт рефералов", callback_data=f"admin_referrer_export_{referrer_id}")],
                            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_referrers")]
                        ])
                        
                        try:
                            await callback.message.edit_text(detail_text, parse_mode="HTML", reply_markup=kb)
                        except:
                            await callback.message.answer(detail_text, parse_mode="HTML", reply_markup=kb)
                    else:
                        await callback.answer(f"❌ Ошибка: {data.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка сервера", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка получения детальной статистики реферала: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer()


@router.callback_query(F.data == "admin_referrers_search")
async def admin_referrers_search_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик поиска рефереров"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await callback.message.answer(
        "🔍 <b>Поиск рефереров</b>\n\n"
        "Введите username, имя, фамилию или ID пользователя для поиска:\n"
        "💡 <i>Для отмены введите /cancel</i>",
        parse_mode="HTML"
    )
    
    await state.set_state(ReferrersSearchState.waiting_search)
    await callback.answer()


@router.message(ReferrersSearchState.waiting_search)
async def handle_referrers_search(message: Message, state: FSMContext):
    """Обработка поискового запроса по реферерам"""
    user_id = message.from_user.id

    if not is_admin(user_id):
        await state.clear()
        return

    if message.text and message.text.strip().lower() == '/cancel':
        await message.answer("❌ Поиск отменен")
        await state.clear()
        return

    search_query = (message.text or "").strip()
    if not search_query:
        await message.answer("❌ Введите username, имя, фамилию или ID пользователя для поиска.")
        return

    class FakeCallback:
        def __init__(self, msg):
            self.message = msg
            self.from_user = msg.from_user
        async def answer(self, *args, **kwargs):
            pass

    await show_referrers_list(FakeCallback(message), page=1, search_query=search_query)
    await state.clear()


@router.callback_query(F.data.startswith("admin_referrer_export_"))
async def admin_referrer_export_handler(callback: CallbackQuery):
    """Обработчик экспорта рефералов конкретного реферера (только тех, кто купил)"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        referrer_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/referrers/{referrer_id}/export/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    # Получаем CSV файл
                    csv_content = await response.read()
                    
                    # Отправляем файл пользователю
                    from aiogram.types import BufferedInputFile
                    filename = f"referrer_{referrer_id}_referrals.csv"
                    file = BufferedInputFile(csv_content, filename=filename)
                    await callback.message.answer_document(
                        document=file,
                        caption=f"📥 <b>Экспорт рефералов</b>\n\nЭкспортированы только рефералы, которые перешли по ссылке и купили что-то",
                        parse_mode="HTML"
                    )
                    await callback.answer("✅ Файл отправлен")
                else:
                    error_text = await response.text()
                    await callback.answer(f"❌ Ошибка экспорта: {error_text[:50]}", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка экспорта рефералов реферера: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("toggle_key_"))
async def toggle_key_handler(callback: CallbackQuery):
    """Обработчик включения/выключения ключа"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    try:
        parts = callback.data.split("_")
        key_id = int(parts[2])
        is_active = parts[3] == "on"
        
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/keys/toggle/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={
                'key_id': key_id,
                'is_active': is_active
            }) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        await callback.answer(f"✅ Ключ {'включен' if is_active else 'выключен'}", show_alert=True)
                        # Обновляем список ключей
                        await show_keys_list(callback, page=1)
                    else:
                        await callback.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка сервера", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка изменения статуса ключа: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@router.callback_query(F.data.startswith("admin_key_detail_"))
async def admin_key_detail_handler(callback: CallbackQuery):
    """Обработчик детальной информации о ключе"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        key_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/keys/{key_id}/detail/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success'):
                        key = data.get('key', {})
                        
                        status = "✅ Активен" if key.get('is_active') else "❌ Неактивен"
                        sub_type = key.get('subscription_type', 'N/A')
                        key_value = key.get('key_value', 'N/A')
                        remaining = key.get('remaining_activations', 0)
                        total_act = key.get('total_activations', 0)
                        used_act = key.get('used_activations', 0)
                        available = "✅ Доступен" if key.get('is_available') else "❌ Недоступен"
                        
                        key_detail_text = f"""
🔑 <b>Детали ключа</b>

<b>Ключ:</b> {key_value}
<b>Тип подписки:</b> {sub_type}
<b>Статус:</b> {status}
<b>Доступность:</b> {available}

<b>Активации:</b>
• Всего: {total_act}
• Использовано: {used_act}
• Осталось: {remaining}

<i>Выберите действие ⬇️</i>
"""
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🗑️ Удалить ключ", callback_data=f"admin_key_delete_{key_id}")],
                            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_keys_list")]
                        ])
                        
                        try:
                            await callback.message.edit_text(key_detail_text, parse_mode="HTML", reply_markup=kb)
                        except:
                            await callback.message.answer(key_detail_text, parse_mode="HTML", reply_markup=kb)
                    else:
                        await callback.answer(f"❌ Ошибка: {data.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка загрузки ключа", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка получения ключа: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("admin_key_delete_"))
async def admin_key_delete_handler(callback: CallbackQuery):
    """Обработчик удаления ключа"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        key_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/keys/delete/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={'key_id': key_id}) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        await callback.answer("✅ Ключ удален", show_alert=True)
                        # Возвращаемся к списку ключей
                        await show_keys_list(callback, page=1)
                    else:
                        await callback.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка удаления ключа", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка удаления ключа: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("admin_payment_detail_"))
async def admin_payment_detail_handler(callback: CallbackQuery):
    """Обработчик детальной информации о платеже"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/detail/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success'):
                        payment = data.get('payment', {})
                        
                        payment_id_val = payment.get('payment_id', 'N/A')
                        user_id_val = payment.get('user_id', 'N/A')
                        username = payment.get('username', 'N/A') or payment.get('first_name', 'N/A')
                        amount = payment.get('amount', 0)
                        status = payment.get('status', 'N/A')
                        sub_type = payment.get('subscription_type', 'N/A')
                        issued_key = payment.get('issued_key', 'Не выдан')
                        created_at = payment.get('created_at', 'N/A')
                        paid_at = payment.get('paid_at', 'N/A')
                        
                        status_emoji = {
                            'pending': '⏳',
                            'succeeded': '✅',
                            'canceled': '❌',
                            'failed': '❌'
                        }.get(status, '❓')
                        
                        payment_detail_text = f"""
💳 <b>Детали платежа</b>

<b>ID платежа:</b> #{payment_id_val}
<b>Статус:</b> {status_emoji} {status}
<b>Сумма:</b> {amount} ₽
<b>Тип подписки:</b> {sub_type}

<b>Пользователь:</b>
• ID: {user_id_val}
• Имя: {username}

<b>Ключ:</b> {issued_key if issued_key != 'Не выдан' else '❌ Не выдан'}

<b>Даты:</b>
• Создан: {created_at[:19] if created_at != 'N/A' else 'N/A'}
• Оплачен: {paid_at[:19] if paid_at else 'Не оплачен'}

<i>Выберите действие ⬇️</i>
"""
                        
                        kb_buttons = []
                        if status == 'pending':
                            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить платеж", callback_data=f"admin_payment_confirm_{payment_id_val}")])
                        kb_buttons.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_payments")])
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
                        
                        try:
                            await callback.message.edit_text(payment_detail_text, parse_mode="HTML", reply_markup=kb)
                        except:
                            await callback.message.answer(payment_detail_text, parse_mode="HTML", reply_markup=kb)
                    else:
                        await callback.answer(f"❌ Ошибка: {data.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка загрузки платежа", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка получения платежа: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data.startswith("admin_payment_confirm_"))
async def admin_payment_confirm_handler(callback: CallbackQuery):
    """Обработчик подтверждения платежа"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split("_")[-1])
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/confirm/'
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        await callback.answer("✅ Платеж подтвержден", show_alert=True)
                        # Показываем обновленную детальную информацию
                        # Получаем детальную информацию о платеже
                        detail_api_url = f'{DJANGO_API_URL}/bot_management/api/payments/{payment_id}/detail/'
                        async with session.get(detail_api_url) as detail_response:
                            if detail_response.status == 200:
                                detail_data = await detail_response.json()
                                if detail_data.get('success'):
                                    payment = detail_data.get('payment', {})
                                    
                                    payment_id_val = payment.get('payment_id', 'N/A')
                                    user_id_val = payment.get('user_id', 'N/A')
                                    username = payment.get('username', 'N/A') or payment.get('first_name', 'N/A')
                                    amount = payment.get('amount', 0)
                                    status = payment.get('status', 'N/A')
                                    sub_type = payment.get('subscription_type', 'N/A')
                                    issued_key = payment.get('issued_key', 'Не выдан')
                                    created_at = payment.get('created_at', 'N/A')
                                    paid_at = payment.get('paid_at', 'N/A')
                                    
                                    status_emoji = {
                                        'pending': '⏳',
                                        'succeeded': '✅',
                                        'canceled': '❌',
                                        'failed': '❌'
                                    }.get(status, '❓')
                                    
                                    payment_detail_text = f"""
💳 <b>Детали платежа</b>

<b>ID платежа:</b> #{payment_id_val}
<b>Статус:</b> {status_emoji} {status}
<b>Сумма:</b> {amount} ₽
<b>Тип подписки:</b> {sub_type}

<b>Пользователь:</b>
• ID: {user_id_val}
• Имя: {username}

<b>Ключ:</b> {issued_key if issued_key != 'Не выдан' else '❌ Не выдан'}

<b>Даты:</b>
• Создан: {created_at[:19] if created_at != 'N/A' else 'N/A'}
• Оплачен: {paid_at[:19] if paid_at else 'Не оплачен'}

<i>Платеж подтвержден ✅</i>
"""
                                    
                                    kb = InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admin_payments")]
                                    ])
                                    
                                    try:
                                        await callback.message.edit_text(payment_detail_text, parse_mode="HTML", reply_markup=kb)
                                    except:
                                        await callback.message.answer(payment_detail_text, parse_mode="HTML", reply_markup=kb)
                    else:
                        await callback.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка подтверждения платежа", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка подтверждения платежа: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer()

@router.callback_query(F.data == "admin_toggle_manager_key_delivery")
async def admin_toggle_manager_key_delivery_handler(callback: CallbackQuery):
    """Обработчик включения/выключения функции 'Написать менеджеру'"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    try:
        import aiohttp
        
        # Получаем текущее значение
        api_url_get = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url_get) as response:
                if response.status == 200:
                    data = await response.json()
                    current_value = data.get('value', 'true')
                    if current_value is None:
                        current_value = 'true'
                    new_value = 'false' if str(current_value).lower() == 'true' else 'true'
                    
                    # Обновляем значение
                    api_url_update = f'{DJANGO_API_URL}/bot_management/api/settings/update/'
                    async with session.post(api_url_update, json={
                        'key': 'manager_key_delivery_enabled',
                        'value': new_value
                    }) as update_response:
                        if update_response.status == 200:
                            result = await update_response.json()
                            if result.get('success'):
                                status_text = "включена" if new_value == 'true' else "выключена"
                                await callback.answer(f"✅ Функция 'Получить ключ у менеджера' {status_text}", show_alert=True)
                                # Возвращаемся в настройки
                                await admin_settings_handler(callback)
                            else:
                                await callback.answer(f"❌ Ошибка: {result.get('message', 'Неизвестная ошибка')}", show_alert=True)
                        else:
                            await callback.answer("❌ Ошибка обновления настройки", show_alert=True)
                else:
                    await callback.answer("❌ Ошибка получения настройки", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка переключения функции менеджера: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@router.callback_query(F.data == "admin_find_user")
async def admin_find_user_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик поиска пользователя"""
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    await state.update_data(action='find_user')
    await callback.message.answer(
        "🔍 <b>Поиск пользователя</b>\n\nВведите ID или username пользователя для поиска:",
        parse_mode="HTML"
    )

    await state.set_state(AdminState.waiting_user_id)
    await callback.answer()

@router.callback_query(F.data == "admin_settings")
async def admin_settings_handler(callback: CallbackQuery):
    """Обработчик настроек"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    if not DJANGO_INTEGRATION:
        await callback.answer("❌ Система недоступна", show_alert=True)
        return
    
    # Получаем текущие цены
    try:
        import aiohttp
        
        api_url = f'{DJANGO_API_URL}/bot_management/api/prices/get/'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get('prices', {})
                    
                    settings_text = f"""
⚙️ <b>Настройки</b>

💰 <b>Текущие цены:</b>
• Месячная: <b>{prices.get('month', 0)} ₽</b>
• 3 месяца: <b>{prices.get('3months', 0)} ₽</b>
• 6 месяцев: <b>{prices.get('6months', 0)} ₽</b>
• Годовая: <b>{prices.get('year', 0)} ₽</b>

<i>Выберите действие ⬇️</i>
"""
                else:
                    settings_text = "❌ <b>Ошибка загрузки данных</b>"
    except Exception as e:
        logging.error(f"Ошибка получения цен: {e}")
        settings_text = "❌ <b>Ошибка загрузки данных</b>"
    
    # Проверяем статус функции "Написать менеджеру"
    manager_key_delivery_enabled = True
    try:
        import aiohttp
        api_url = f'{DJANGO_API_URL}/bot_management/api/settings/get/?key=manager_key_delivery_enabled'
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success'):
                        value = data.get('value')
                        if value is not None:
                            manager_key_delivery_enabled = str(value).lower() == 'true'
    except:
        pass
    
    kb_buttons = [
        [InlineKeyboardButton(text="💰 Изменить цены", callback_data="admin_edit_prices")],
        [InlineKeyboardButton(text="📝 Изменить названия", callback_data="admin_edit_names")],
        [InlineKeyboardButton(text="📞 Получить ключ у менеджера", callback_data="admin_toggle_manager_key_delivery")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ]
    
    # Обновляем текст кнопки в зависимости от статуса
    manager_status_text = "✅ Включено" if manager_key_delivery_enabled else "❌ Выключено"
    kb_buttons[2][0] = InlineKeyboardButton(
        text=f"📞 Получить ключ у менеджера ({manager_status_text})",
        callback_data="admin_toggle_manager_key_delivery"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    try:
        await callback.message.edit_text(settings_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(settings_text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data == "admin_scheduler")
async def admin_scheduler_handler(callback: CallbackQuery):
    """Обработчик статуса планировщика уведомлений"""
    user_id = callback.from_user.id

    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    # Получаем статус планировщика
    status = get_scheduler_status()

    scheduler_text = f"""
🔔 <b>Статус планировщика уведомлений</b>

📊 <b>Общий статус:</b> {'🟢 Запущен' if status.get('status') == 'running' else '🔴 Остановлен'}

🎯 <b>Активных задач:</b> {status.get('jobs_count', 0)}

📋 <b>Запланированные задачи:</b>
"""

    if status.get('jobs'):
        for job in status['jobs']:
            next_run = job.get('next_run', 'Не запланировано')
            if next_run != 'Не запланировано':
                # Преобразуем время в читаемый формат
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
                    next_run = dt.strftime('%d.%m.%Y %H:%M')
                except:
                    pass

            scheduler_text += f"\n🔸 <b>{job['name']}</b>\n   🕐 Следующий запуск: {next_run}\n   ⏰ {job['trigger']}\n"
    else:
        scheduler_text += "\n❌ Задачи не найдены"

    scheduler_text += """

💡 <b>Что делает планировщик:</b>
• 💳 Отправляет напоминания о платежах (каждые 30 мин)
• 🔑 Проверяет остаток ключей (каждый час)
• 📅 Напоминает о заканчивающихся подписках (10:00 ежедневно)
"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])

    try:
        await callback.message.edit_text(scheduler_text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(scheduler_text, parse_mode="HTML", reply_markup=kb)

    await callback.answer()

@router.callback_query(F.data == "admin_edit_prices")
async def admin_edit_prices_handler(callback: CallbackQuery):
    """Выбор подписки для изменения цены"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    text = """
💰 <b>Изменение цен</b>

📝 <b>Выберите подписку для изменения цены:</b>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Месячная", callback_data="edit_price_month")],
        [InlineKeyboardButton(text="📅 3 месяца", callback_data="edit_price_3months")],
        [InlineKeyboardButton(text="📅 Годовая", callback_data="edit_price_year")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data.startswith("edit_price_"))
async def admin_edit_price_type_handler(callback: CallbackQuery, state: FSMContext):
    """Запрос новой цены"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    sub_type = callback.data.replace("edit_price_", "")
    
    await state.update_data(subscription_type=sub_type, action='edit_price')
    
    text = f"""
💰 <b>Изменение цены</b>

📅 <b>Подписка:</b> {sub_type}

📝 <b>Отправьте новую цену в рублях:</b>

<i>Пример: 390</i>

<i>Для отмены отправьте /cancel</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_edit_prices")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await state.set_state(AdminState.waiting_user_id)
    await callback.answer()

@router.callback_query(F.data == "admin_edit_names")
async def admin_edit_names_handler(callback: CallbackQuery):
    """Выбор подписки для изменения названия"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    text = """
📝 <b>Изменение названий</b>

📝 <b>Выберите подписку для изменения названия:</b>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Месячная", callback_data="edit_name_month")],
        [InlineKeyboardButton(text="📅 3 месяца", callback_data="edit_name_3months")],
        [InlineKeyboardButton(text="📅 Годовая", callback_data="edit_name_year")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await callback.answer()

@router.callback_query(F.data.startswith("edit_name_"))
async def admin_edit_name_type_handler(callback: CallbackQuery, state: FSMContext):
    """Запрос нового названия"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    sub_type = callback.data.replace("edit_name_", "")
    
    await state.update_data(subscription_type=sub_type, action='edit_name')
    
    text = f"""
📝 <b>Изменение названия</b>

📅 <b>Подписка:</b> {sub_type}

📝 <b>Отправьте новое название:</b>

<i>Пример: Месячная подписка</i>

<i>Для отмены отправьте /cancel</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_edit_names")]
    ])
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    
    await state.set_state(AdminState.waiting_user_id)
    await callback.answer()

@router.callback_query(F.data.startswith("retry_payment:"))
async def retry_payment_handler(callback: CallbackQuery):
    """Обработчик кнопки 'Создать новый платеж' из напоминания о незавершенном платеже"""
    user_id = callback.from_user.id

    try:
        # Получаем тип подписки из callback_data
        subscription_type = callback.data.replace("retry_payment:", "")

        # Проверяем корректность типа подписки
        if subscription_type not in ['trial', 'month', '3months', '6months', 'year']:
            await callback.answer("❌ Неверный тип подписки", show_alert=True)
            return

        # Определяем название подписки
        sub_names = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }
        sub_name = sub_names.get(subscription_type, 'Подписка')

        # Создаем новый платеж
        text = f"""
🔄 <b>Создание нового платежа</b>

📅 <b>Тип подписки:</b> {sub_name}

💡 <b>Выберите способ оплаты:</b>

<i>Выберите удобный для вас способ оплаты ниже ⬇️</i>
"""

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить по СБП", callback_data=f"pay_card_{subscription_type}")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])

        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

        await callback.answer("Создаем новый платеж...")

    except Exception as e:
        logging.error(f"Ошибка в retry_payment_handler: {e}")
        await callback.answer("❌ Ошибка создания платежа", show_alert=True)


async def main():
    # Добавим админов в БД
    with get_db() as conn:
        for aid in ADMIN_IDS:
            conn.execute("INSERT OR IGNORE INTO admin_users (admin_id) VALUES (?)", (aid,))

    # Кэшируем все изображения (один раз при старте, потом file_id мгновенный)
    await cache_all_images()

    # Запускаем планировщик уведомлений
    await start_notification_scheduler()

    # Запускаем HTTP сервер для получения уведомлений от Django
    await init_http_server()

    # Изображения будут загружаться по требованию
    logging.info("🚀 Бот готов к работе")
    logging.info("🔔 Планировщик уведомлений запущен")

    try:
        # Запуск polling с улучшенными настройками для устойчивости к DDoS
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "inline_query"],
            drop_pending_updates=True,  # Игнорируем старые обновления при запуске
            timeout=30,  # Таймаут для long polling (секунды)
            relax=1.0,  # Задержка между запросами (секунды)
            fast=True,  # Оптимизированный режим
        )
    except KeyboardInterrupt:
        logging.info("Получен сигнал прерывания...")
    except Exception as e:
        logging.error(f"Ошибка работы бота: {e}")
    finally:
        # Корректная остановка планировщика
        await stop_notification_scheduler()
        logging.info("🔔 Планировщик уведомлений остановлен")

if __name__ == "__main__":
    asyncio.run(main())


# ============================================================================
# АДМИНКА ДЛЯ ОБЫЧНОГО VPN (в боте)
# ============================================================================

@router.callback_query(F.data == "admin_regular_vpn_stats")
async def admin_regular_vpn_stats(callback: CallbackQuery):
    """Статистика Обычного VPN"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    from bot_management.models import Payment
    from django.db.models import Sum
    from asgiref.sync import sync_to_async
    
    # Получаем статистику через sync_to_async
    total_payments = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', status='succeeded').count()
    )()
    total_amount_result = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', status='succeeded').aggregate(total=Sum('amount'))
    )()
    total_amount = total_amount_result['total'] or 0
    
    # Детализация по типам
    day_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_day', status='succeeded').count()
    )()
    month_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_month', status='succeeded').count()
    )()
    months_3_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_3months', status='succeeded').count()
    )()
    months_6_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_6months', status='succeeded').count()
    )()
    year_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_year', status='succeeded').count()
    )()
    years_2_count = await sync_to_async(
        lambda: Payment.objects.filter(vpn_type='regular', subscription_type='regular_2years', status='succeeded').count()
    )()
    
    stats_text = f"""
📊 <b>Статистика Обычного VPN</b>

💰 <b>Всего платежей:</b> {total_payments}
💵 <b>Общая сумма:</b> {total_amount}₽

📅 <b>По типам подписок:</b>
• 1 день: {day_count}
• 1 месяц: {month_count}
• 3 месяца: {months_3_count}
• 6 месяцев: {months_6_count}
• 1 год: {year_count}
• 2 года: {years_2_count}
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выплаты", callback_data="admin_regular_vpn_payouts")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, stats_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "admin_regular_vpn_payouts")
async def admin_regular_vpn_payouts(callback: CallbackQuery):
    """Список выплат Обычного VPN"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    from bot_management.models import RegularVpnPayout
    from asgiref.sync import sync_to_async
    
    # Получаем последние 10 выплат
    payouts = await sync_to_async(
        lambda: list(RegularVpnPayout.objects.all().order_by('-created_at')[:10])
    )()
    
    if payouts:
        payouts_text = "💰 <b>Выплаты Обычного VPN</b>\n\n"
        for payout in payouts:
            status_emoji = {"pending": "⏳", "fixed": "✅", "paid": "💵"}.get(payout.status, "❓")
            payouts_text += f"{status_emoji} #{payout.payout_id} - {payout.total_amount}₽ - {payout.get_status_display()}\n"
            if payout.fixed_at:
                payouts_text += f"   Зафиксировано: {payout.fixed_at.strftime('%d.%m.%Y')}\n"
    else:
        payouts_text = "💰 <b>Выплаты Обычного VPN</b>\n\n📭 Выплат пока нет"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать выплату", callback_data="admin_regular_vpn_payout_create")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_regular_vpn_stats")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await send_or_edit_message_with_photo(callback, payouts_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data == "admin_regular_vpn_payout_create")
async def admin_regular_vpn_payout_create(callback: CallbackQuery):
    """Создание новой выплаты"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    from bot_management.models import RegularVpnPayout
    from asgiref.sync import sync_to_async
    
    # Создаем новую выплату
    payout = await sync_to_async(RegularVpnPayout.objects.create)()
    await sync_to_async(payout.calculate_from_payments)()
    
    payout_text = f"""
💰 <b>Новая выплата создана</b>

📊 <b>Статистика:</b>
• Всего платежей: {payout.total_payments}
• Общая сумма: {payout.total_amount}₽

📅 <b>По типам:</b>
• 1 день: {payout.regular_day_count}
• 1 месяц: {payout.regular_month_count}
• 3 месяца: {payout.regular_3months_count}
• 6 месяцев: {payout.regular_6months_count}
• 1 год: {payout.regular_year_count}
• 2 года: {payout.regular_2years_count}

⏳ <b>Статус:</b> Ожидает фиксации
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Зафиксировать", callback_data=f"admin_regular_vpn_payout_fix_{payout.payout_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_regular_vpn_payouts")]
    ])
    
    await send_or_edit_message_with_photo(callback, payout_text, reply_markup=kb, edit_message=True, image_name="catalog.png")


@router.callback_query(F.data.startswith("admin_regular_vpn_payout_fix_"))
async def admin_regular_vpn_payout_fix(callback: CallbackQuery):
    """Фиксация выплаты"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    from bot_management.models import RegularVpnPayout
    from django.utils import timezone
    from asgiref.sync import sync_to_async
    
    payout_id = int(callback.data.split("_")[-1])
    
    try:
        payout = await sync_to_async(RegularVpnPayout.objects.get)(payout_id=payout_id)
        
        # СНАЧАЛА считаем статистику (до фиксации!)
        await sync_to_async(payout.calculate_from_payments)()
        
        # ПОТОМ фиксируем
        payout.status = 'fixed'
        payout.fixed_at = timezone.now()
        await sync_to_async(payout.save)()
        
        payout_text = f"""
✅ <b>Выплата зафиксирована</b>

💰 <b>Сумма:</b> {payout.total_amount}₽
📊 <b>Платежей:</b> {payout.total_payments}
📅 <b>Зафиксировано:</b> {payout.fixed_at.strftime('%d.%m.%Y %H:%M')}

Теперь можно отметить как выплаченную.
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Выплачено", callback_data=f"admin_regular_vpn_payout_paid_{payout.payout_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_regular_vpn_payouts")]
        ])
        
        await send_or_edit_message_with_photo(callback, payout_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
        
    except RegularVpnPayout.DoesNotExist:
        await callback.answer("❌ Выплата не найдена", show_alert=True)


@router.callback_query(F.data.startswith("admin_regular_vpn_payout_paid_"))
async def admin_regular_vpn_payout_paid(callback: CallbackQuery):
    """Отметка выплаты как выплаченной"""
    user_id = callback.from_user.id
    
    if not is_admin(user_id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    from bot_management.models import RegularVpnPayout
    from django.utils import timezone
    from asgiref.sync import sync_to_async
    
    payout_id = int(callback.data.split("_")[-1])
    
    try:
        payout = await sync_to_async(RegularVpnPayout.objects.get)(payout_id=payout_id)
        payout.status = 'paid'
        payout.paid_at = timezone.now()
        await sync_to_async(payout.save)()
        
        payout_text = f"""
💵 <b>Выплата отмечена как выплаченная</b>

💰 <b>Сумма:</b> {payout.total_amount}₽
📅 <b>Выплачено:</b> {payout.paid_at.strftime('%d.%m.%Y %H:%M')}
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_regular_vpn_payouts")]
        ])
        
        await send_or_edit_message_with_photo(callback, payout_text, reply_markup=kb, edit_message=True, image_name="catalog.png")
        
    except RegularVpnPayout.DoesNotExist:
        await callback.answer("❌ Выплата не найдена", show_alert=True)

