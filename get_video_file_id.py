#!/usr/bin/env python3
"""
Скрипт для получения file_id видео в Telegram
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from config import BOT_TOKEN

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создание бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("get_file_id"))
async def get_video_file_id(message: Message):
    """Получает file_id видео из сообщения"""
    if message.video:
        file_id = message.video.file_id
        file_size = message.video.file_size
        duration = message.video.duration
        width = message.video.width
        height = message.video.height
        
        response = f"""
🎥 <b>Информация о видео:</b>

📁 <b>File ID:</b> <code>{file_id}</code>
📏 <b>Размер:</b> {file_size:,} байт
⏱️ <b>Длительность:</b> {duration} сек
📐 <b>Разрешение:</b> {width}x{height}

✅ <b>Скопируйте file_id и добавьте в .env файл:</b>
<code>VIDEO_FILE_ID={file_id}</code>
"""
        
        await message.answer(response, parse_mode="HTML")
        logger.info(f"Получен file_id: {file_id}")
        
    elif message.document and message.document.mime_type.startswith('video/'):
        file_id = message.document.file_id
        file_name = message.document.file_name
        file_size = message.document.file_size
        
        response = f"""
🎥 <b>Информация о видео-документе:</b>

📁 <b>File ID:</b> <code>{file_id}</code>
📄 <b>Имя файла:</b> {file_name}
📏 <b>Размер:</b> {file_size:,} байт

✅ <b>Скопируйте file_id и добавьте в .env файл:</b>
<code>VIDEO_FILE_ID={file_id}</code>
"""
        
        await message.answer(response, parse_mode="HTML")
        logger.info(f"Получен file_id документа: {file_id}")
        
    else:
        await message.answer(
            "❌ Пожалуйста, отправьте видео файл для получения file_id.\n\n"
            "Поддерживаемые форматы: MP4, AVI, MOV, MKV"
        )

@dp.message(Command("test_video"))
async def test_video_sending(message: Message):
    """Тестирует отправку видео различными способами"""
    from video_utils import VideoSender
    
    sender = VideoSender(bot)
    
    # Тест 1: Проверяем VIDEO_FILE_ID из конфига
    from config import VIDEO_FILE_ID
    if VIDEO_FILE_ID and VIDEO_FILE_ID.strip() != "your_video_file_id_here":
        success = await sender.send_video_by_file_id(
            chat_id=message.chat.id,
            file_id=VIDEO_FILE_ID,
            caption="🎥 Тест отправки по file_id"
        )
        
        if success:
            await message.answer("✅ Видео по file_id отправлено успешно!")
        else:
            await message.answer("❌ Ошибка отправки видео по file_id")
    else:
        await message.answer("⚠️ VIDEO_FILE_ID не настроен в .env файле")
    
    # Тест 2: Проверяем файлы в папке videos
    import os
    video_files = ['video.mp4', 'welcome.mp4', 'intro.mp4', 'demo.mp4']
    
    for video_file in video_files:
        video_path = f"videos/{video_file}"
        if os.path.exists(video_path):
            success = await sender.send_video_from_file(
                chat_id=message.chat.id,
                file_path=video_path,
                caption=f"🎥 Тест отправки из файла: {video_file}"
            )
            
            if success:
                await message.answer(f"✅ Видео из файла {video_file} отправлено успешно!")
                break
    else:
        await message.answer("⚠️ Файлы видео в папке videos/ не найдены")

@dp.message(Command("help_video"))
async def help_video(message: Message):
    """Помощь по настройке видео"""
    help_text = """
🎥 <b>Помощь по настройке видео</b>

<b>Команды:</b>
/get_file_id - Получить file_id из отправленного видео
/test_video - Протестировать отправку видео
/help_video - Показать эту справку

<b>Способы настройки видео:</b>

1️⃣ <b>По file_id (рекомендуется):</b>
   • Отправьте видео боту командой /get_file_id
   • Скопируйте полученный file_id
   • Добавьте в .env: VIDEO_FILE_ID=ваш_file_id

2️⃣ <b>Из файла на сервере:</b>
   • Поместите видео в папку videos/
   • Поддерживаемые имена: video.mp4, welcome.mp4, intro.mp4, demo.mp4

3️⃣ <b>По URL:</b>
   • Укажите URL видео в коде бота

<b>Рекомендации:</b>
• Формат: MP4
• Размер: до 50 МБ
• Длительность: до 60 сек
• Разрешение: 1280x720 или меньше
"""
    
    await message.answer(help_text, parse_mode="HTML")

async def main():
    """Запуск бота для получения file_id"""
    logger.info("Запуск бота для получения file_id видео...")
    logger.info("Отправьте команду /help_video для получения справки")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())








