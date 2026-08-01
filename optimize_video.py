#!/usr/bin/env python3
"""
Скрипт для оптимизации видео для быстрой отправки в Telegram
"""
import os
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def optimize_video_for_telegram(input_path: str, output_path: str = None) -> bool:
    """
    Оптимизирует видео для быстрой отправки в Telegram
    
    Параметры оптимизации:
    - H.264 кодек (лучшая совместимость)
    - Низкий битрейт (быстрая загрузка)
    - Разрешение до 1280x720
    - Длительность до 60 секунд
    """
    try:
        if not os.path.exists(input_path):
            logger.error(f"Файл не найден: {input_path}")
            return False
        
        if output_path is None:
            name, ext = os.path.splitext(input_path)
            output_path = f"{name}_optimized{ext}"
        
        # Команда ffmpeg для оптимизации
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', 'libx264',  # H.264 кодек
            '-preset', 'fast',   # Быстрое кодирование
            '-crf', '28',        # Высокое сжатие (быстрая загрузка)
            '-maxrate', '1M',    # Максимальный битрейт 1 Мбит/с
            '-bufsize', '2M',    # Размер буфера
            '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease',  # Масштабирование
            '-c:a', 'aac',       # AAC аудио
            '-b:a', '128k',       # Аудио битрейт
            '-movflags', '+faststart',  # Быстрый старт
            '-y',                # Перезаписать файл
            output_path
        ]
        
        logger.info(f"Оптимизация видео: {input_path} -> {output_path}")
        logger.info("Это может занять некоторое время...")
        
        # Запускаем ffmpeg
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"✅ Видео оптимизировано: {output_path}")
            
            # Показываем размеры файлов
            original_size = os.path.getsize(input_path)
            optimized_size = os.path.getsize(output_path)
            compression_ratio = (1 - optimized_size / original_size) * 100
            
            logger.info(f"📊 Оригинал: {original_size:,} байт")
            logger.info(f"📊 Оптимизировано: {optimized_size:,} байт")
            logger.info(f"📊 Сжатие: {compression_ratio:.1f}%")
            
            return True
        else:
            logger.error(f"Ошибка ffmpeg: {result.stderr}")
            return False
            
    except FileNotFoundError:
        logger.error("❌ ffmpeg не найден. Установите ffmpeg для оптимизации видео.")
        logger.info("💡 Скачайте ffmpeg с https://ffmpeg.org/download.html")
        return False
    except Exception as e:
        logger.error(f"Ошибка оптимизации: {e}")
        return False

def get_video_info(video_path: str) -> dict:
    """Получает информацию о видео"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            
            # Находим видео поток
            video_stream = None
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break
            
            if video_stream:
                return {
                    'duration': float(data.get('format', {}).get('duration', 0)),
                    'size': int(data.get('format', {}).get('size', 0)),
                    'width': int(video_stream.get('width', 0)),
                    'height': int(video_stream.get('height', 0)),
                    'bitrate': int(data.get('format', {}).get('bit_rate', 0)),
                    'codec': video_stream.get('codec_name', 'unknown')
                }
        
        return {}
        
    except Exception as e:
        logger.error(f"Ошибка получения информации о видео: {e}")
        return {}

def check_telegram_limits(video_path: str) -> dict:
    """Проверяет соответствие видео ограничениям Telegram"""
    info = get_video_info(video_path)
    
    limits = {
        'max_size': 50 * 1024 * 1024,  # 50 МБ
        'max_duration': 60,  # 60 секунд
        'max_width': 1280,
        'max_height': 720
    }
    
    issues = []
    
    if info.get('size', 0) > limits['max_size']:
        issues.append(f"Размер файла {info['size']:,} байт превышает лимит {limits['max_size']:,} байт")
    
    if info.get('duration', 0) > limits['max_duration']:
        issues.append(f"Длительность {info['duration']:.1f} сек превышает лимит {limits['max_duration']} сек")
    
    if info.get('width', 0) > limits['max_width']:
        issues.append(f"Ширина {info['width']}px превышает лимит {limits['max_width']}px")
    
    if info.get('height', 0) > limits['max_height']:
        issues.append(f"Высота {info['height']}px превышает лимит {limits['max_height']}px")
    
    return {
        'info': info,
        'issues': issues,
        'needs_optimization': len(issues) > 0
    }

def main():
    """Основная функция"""
    print("🎥 Оптимизация видео для Telegram")
    print("=" * 50)
    
    # Проверяем наличие ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("✅ ffmpeg найден")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ ffmpeg не найден")
        print("💡 Установите ffmpeg для оптимизации видео")
        print("   Скачайте с https://ffmpeg.org/download.html")
        return
    
    # Проверяем файлы в папке videos
    videos_dir = Path("videos")
    if not videos_dir.exists():
        print("📁 Создаем папку videos/")
        videos_dir.mkdir()
    
    video_files = list(videos_dir.glob("*.mp4")) + list(videos_dir.glob("*.avi")) + list(videos_dir.glob("*.mov"))
    
    if not video_files:
        print("⚠️ Видео файлы не найдены в папке videos/")
        print("💡 Поместите видео файлы в папку videos/ для оптимизации")
        return
    
    print(f"📁 Найдено видео файлов: {len(video_files)}")
    
    for video_file in video_files:
        print(f"\n🎬 Обработка: {video_file.name}")
        
        # Проверяем ограничения Telegram
        limits_check = check_telegram_limits(str(video_file))
        
        if limits_check['info']:
            info = limits_check['info']
            print(f"📊 Информация о видео:")
            print(f"   Размер: {info.get('size', 0):,} байт")
            print(f"   Длительность: {info.get('duration', 0):.1f} сек")
            print(f"   Разрешение: {info.get('width', 0)}x{info.get('height', 0)}")
            print(f"   Кодек: {info.get('codec', 'unknown')}")
        
        if limits_check['issues']:
            print("⚠️ Проблемы с ограничениями Telegram:")
            for issue in limits_check['issues']:
                print(f"   - {issue}")
            
            # Предлагаем оптимизацию
            response = input("🤔 Оптимизировать видео? (y/n): ").lower().strip()
            if response == 'y':
                output_path = videos_dir / f"{video_file.stem}_optimized{video_file.suffix}"
                if optimize_video_for_telegram(str(video_file), str(output_path)):
                    print(f"✅ Оптимизированное видео сохранено: {output_path.name}")
                else:
                    print("❌ Ошибка оптимизации")
        else:
            print("✅ Видео соответствует ограничениям Telegram")

if __name__ == "__main__":
    main()








