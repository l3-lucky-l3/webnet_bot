#!/usr/bin/env python3
"""
Пример использования системы отправки видео
"""
import asyncio
import logging
from aiogram import Bot
from video_utils import VideoSender
from config import BOT_TOKEN

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def example_send_video():
    """Пример отправки видео различными способами"""
    
    # Создание бота
    bot = Bot(token=BOT_TOKEN)
    sender = VideoSender(bot)
    
    # ID чата для тестирования (замените на ваш)
    test_chat_id = 123456789  # Замените на реальный ID
    
    try:
        # Пример 1: Отправка по file_id
        print("🎥 Пример 1: Отправка по file_id")
        success = await sender.send_video_by_file_id(
            chat_id=test_chat_id,
            file_id="BAADBAADrwADBREAAYag8mM3ZQABAg",  # Замените на реальный file_id
            caption="🎥 Видео отправлено по file_id",
            reply_markup=None
        )
        print(f"Результат: {'✅ Успешно' if success else '❌ Ошибка'}")
        
        # Пример 2: Отправка из файла
        print("\n🎥 Пример 2: Отправка из файла")
        success = await sender.send_video_from_file(
            chat_id=test_chat_id,
            file_path="videos/welcome.mp4",
            caption="🎥 Видео отправлено из файла",
            reply_markup=None,
            width=1280,
            height=720,
            duration=30
        )
        print(f"Результат: {'✅ Успешно' if success else '❌ Ошибка'}")
        
        # Пример 3: Отправка по URL
        print("\n🎥 Пример 3: Отправка по URL")
        success = await sender.send_video_from_url(
            chat_id=test_chat_id,
            video_url="https://example.com/video.mp4",
            caption="🎥 Видео отправлено по URL",
            reply_markup=None
        )
        print(f"Результат: {'✅ Успешно' if success else '❌ Ошибка'}")
        
        # Пример 4: Умная отправка
        print("\n🎥 Пример 4: Умная отправка")
        success = await sender.send_video_smart(
            chat_id=test_chat_id,
            video_source="videos/welcome.mp4",  # Автоматически определит тип
            caption="🎥 Умная отправка видео",
            reply_markup=None
        )
        print(f"Результат: {'✅ Успешно' if success else '❌ Ошибка'}")
        
    except Exception as e:
        logger.error(f"Ошибка в примере: {e}")
    
    finally:
        await bot.session.close()

async def example_get_file_id():
    """Пример получения file_id из видео"""
    from aiogram import Bot
    from aiogram.types import FSInputFile
    
    bot = Bot(token=BOT_TOKEN)
    
    try:
        # Отправляем видео и получаем file_id
        video_file = FSInputFile("videos/welcome.mp4")
        
        # В реальном использовании file_id можно получить из ответа бота
        # или из логов Telegram API
        print("📁 Для получения file_id:")
        print("1. Запустите get_video_file_id.py")
        print("2. Отправьте команду /get_file_id")
        print("3. Прикрепите ваше видео")
        print("4. Скопируйте полученный file_id")
        
    except Exception as e:
        logger.error(f"Ошибка получения file_id: {e}")
    
    finally:
        await bot.session.close()

if __name__ == "__main__":
    print("🎥 Примеры использования системы отправки видео")
    print("=" * 50)
    
    print("\n1. Пример отправки видео:")
    print("   Замените test_chat_id на реальный ID чата")
    print("   Раскомментируйте нужные примеры")
    
    print("\n2. Получение file_id:")
    asyncio.run(example_get_file_id())
    
    print("\n3. Запуск бота для получения file_id:")
    print("   python get_video_file_id.py")
    
    # Раскомментируйте для тестирования отправки
    # asyncio.run(example_send_video())








