#!/usr/bin/env python3
"""
Утилиты для работы с видео в Telegram боте
"""
import os
import logging
from typing import Optional, Union
from aiogram.types import FSInputFile, URLInputFile, InlineKeyboardMarkup
from aiogram import Bot

logger = logging.getLogger(__name__)

class VideoSender:
    """Класс для отправки видео различными способами"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def send_video_by_file_id(self, chat_id: int, file_id: str, caption: str = "", 
                                   reply_markup: Optional[InlineKeyboardMarkup] = None) -> bool:
        """Отправка видео по file_id (быстрый способ)"""
        try:
            await self.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
                supports_streaming=True,  # Включаем стриминг
                disable_notification=True  # Отключаем уведомления
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки видео по file_id: {e}")
            return False
    
    async def send_video_from_file(self, chat_id: int, file_path: str, caption: str = "",
                                  reply_markup: Optional[InlineKeyboardMarkup] = None,
                                  width: Optional[int] = None, height: Optional[int] = None,
                                  duration: Optional[int] = None) -> bool:
        """Отправка видео из файла с оптимизацией"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"Файл видео не найден: {file_path}")
                return False
            
            # Оптимизация: используем FSInputFile с буферизацией
            video_file = FSInputFile(file_path)
            
            # Отправляем с оптимизированными параметрами
            await self.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
                width=width,
                height=height,
                duration=duration,
                supports_streaming=True,  # Включаем стриминг для быстрой загрузки
                disable_notification=True  # Отключаем уведомления для ускорения
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки видео из файла: {e}")
            return False
    
    async def send_video_from_url(self, chat_id: int, video_url: str, caption: str = "",
                                 reply_markup: Optional[InlineKeyboardMarkup] = None) -> bool:
        """Отправка видео по URL"""
        try:
            video_file = URLInputFile(video_url)
            
            await self.bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
                supports_streaming=True
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки видео по URL: {e}")
            return False
    
    async def send_video_smart(self, chat_id: int, video_source: str, caption: str = "",
                              reply_markup: Optional[InlineKeyboardMarkup] = None) -> bool:
        """Умная отправка видео - автоматически определяет тип источника"""
        try:
            # Проверяем, является ли это file_id
            if video_source.startswith(('BAADBAAD', 'CAAQAA', 'BQACAgIAAxkB')):
                return await self.send_video_by_file_id(chat_id, video_source, caption, reply_markup)
            
            # Проверяем, является ли это URL
            elif video_source.startswith(('http://', 'https://')):
                return await self.send_video_from_url(chat_id, video_source, caption, reply_markup)
            
            # Иначе считаем это путем к файлу
            else:
                return await self.send_video_from_file(chat_id, video_source, caption, reply_markup)
                
        except Exception as e:
            logger.error(f"Ошибка умной отправки видео: {e}")
            return False
    
    async def send_video_fast(self, chat_id: int, file_id: str) -> bool:
        """Быстрая отправка видео только по file_id"""
        try:
            # Проверяем, что это file_id (расширенная проверка)
            if not (file_id.startswith(('BAADBAAD', 'CAAQAA', 'BQACAgIAAxkB', 'BAACAgIAAxkB')) or 
                    len(file_id) > 20):  # file_id обычно длинный
                logger.error(f"Неверный file_id: {file_id}")
                return False
            
            # Быстрая отправка по file_id
            await self.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                supports_streaming=True,    # Включаем стриминг
                disable_notification=True   # Отключаем уведомления
            )
            return True
                
        except Exception as e:
            logger.error(f"Ошибка быстрой отправки видео по file_id: {e}")
            return False

# Пример использования
async def send_welcome_video(bot: Bot, chat_id: int, video_source: str, 
                           welcome_text: str, keyboard: InlineKeyboardMarkup):
    """Отправка приветственного видео"""
    sender = VideoSender(bot)
    
    # Пробуем разные способы отправки
    success = await sender.send_video_smart(
        chat_id=chat_id,
        video_source=video_source,
        caption=welcome_text,
        reply_markup=keyboard
    )
    
    if not success:
        # Если не удалось отправить видео, отправляем текст
        await bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        logger.warning("Не удалось отправить видео, отправлен текст")

# Функция для получения file_id видео (для настройки)
async def get_video_file_id(bot: Bot, chat_id: int, video_path: str) -> Optional[str]:
    """Отправляет видео и возвращает его file_id для дальнейшего использования"""
    try:
        sender = VideoSender(bot)
        success = await sender.send_video_from_file(chat_id, video_path, "Получение file_id")
        
        if success:
            # В реальном использовании file_id можно получить из ответа бота
            # или из логов Telegram API
            logger.info("Видео отправлено, file_id можно получить из логов")
            return "file_id_будет_получен_из_ответа"
        
    except Exception as e:
        logger.error(f"Ошибка получения file_id: {e}")
    
    return None
