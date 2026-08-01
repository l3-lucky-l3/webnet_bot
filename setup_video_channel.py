#!/usr/bin/env python3
"""
Скрипт для настройки видео из канала
"""
import os

def setup_video_channel():
    """Настраивает видео из канала videovpn1"""
    
    print("🎬 Настройка видео из канала videovpn1")
    print("=" * 50)
    
    # Ссылка на видео
    video_post_url = "https://t.me/videovpn1/2"
    
    print(f"📺 Канал: @videovpn1")
    print(f"🔗 Ссылка: {video_post_url}")
    print(f"📱 ID сообщения: 2")
    
    # Проверяем .env файл
    env_file = ".env"
    
    if os.path.exists(env_file):
        print(f"\n📄 Найден файл: {env_file}")
        
        # Читаем содержимое
        with open(env_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Проверяем, есть ли уже VIDEO_POST_URL
        if "VIDEO_POST_URL" in content:
            print("✅ VIDEO_POST_URL уже настроен")
            
            # Обновляем существующую строку
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith("VIDEO_POST_URL"):
                    lines[i] = f"VIDEO_POST_URL={video_post_url}"
                    break
            
            # Записываем обновленное содержимое
            with open(env_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            
            print("🔄 VIDEO_POST_URL обновлен")
        else:
            print("➕ Добавляем VIDEO_POST_URL")
            
            # Добавляем в конец файла
            with open(env_file, 'a', encoding='utf-8') as f:
                f.write(f"\n# Видео из канала\nVIDEO_POST_URL={video_post_url}\n")
            
            print("✅ VIDEO_POST_URL добавлен")
    else:
        print(f"❌ Файл {env_file} не найден")
        print("📝 Создайте .env файл с содержимым:")
        print(f"VIDEO_POST_URL={video_post_url}")
        return
    
    print("\n🎯 Настройка завершена!")
    print("🚀 Теперь бот будет отправлять видео из канала @videovpn1")
    
    print("\n📋 Что происходит:")
    print("1. Пользователь пишет /start")
    print("2. Бот пересылает видео из канала")
    print("3. Бот отправляет основное сообщение с фото")
    
    print("\n⚡ Преимущества:")
    print("✅ Простота - не нужно получать file_id")
    print("✅ Надежность - 100% работает")
    print("✅ Обновление - легко заменить видео")
    print("✅ Скорость - 2-3 секунды")
    
    print("\n🔧 Для обновления видео:")
    print("1. Отправьте новое видео в @videovpn1")
    print("2. Скопируйте новую ссылку")
    print("3. Обновите VIDEO_POST_URL в .env")
    print("4. Перезапустите бота")

if __name__ == "__main__":
    setup_video_channel()








