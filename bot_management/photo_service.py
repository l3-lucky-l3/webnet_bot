#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сервис для работы с фотографиями в поддержке
"""

import requests
import logging
from django.core.cache import cache
from config import BOT_TOKEN

logger = logging.getLogger(__name__)

class PhotoService:
    """Сервис для работы с фотографиями Telegram"""
    
    @staticmethod
    def get_photo_url(file_id):
        """
        Получает URL фотографии по file_id
        Использует кэширование для оптимизации
        """
        if not file_id:
            return None
        
        # Проверяем кэш
        cache_key = f"photo_url_{file_id}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return cached_url
        
        try:
            # Получаем file_path через Telegram API
            response = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={'file_id': file_id},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    file_path = data['result']['file_path']
                    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                    
                    # Кэшируем URL на 1 час
                    cache.set(cache_key, photo_url, 3600)
                    
                    logger.info(f"Получен URL фото: {photo_url}")
                    return photo_url
                else:
                    logger.error(f"Ошибка Telegram API: {data}")
            else:
                logger.error(f"HTTP ошибка: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Ошибка получения URL фото {file_id}: {e}")
        
        # Fallback - попробуем использовать file_id напрямую
        fallback_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_id}"
        logger.warning(f"Используем fallback URL: {fallback_url}")
        return fallback_url
    
    @staticmethod
    def is_photo_accessible(photo_url):
        """
        Проверяет, доступна ли фотография по URL
        """
        if not photo_url:
            return False
        
        try:
            response = requests.head(photo_url, timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка проверки доступности фото {photo_url}: {e}")
            return False
    
    @staticmethod
    def get_photo_info(file_id):
        """
        Получает информацию о фотографии
        """
        if not file_id:
            return None
        
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={'file_id': file_id},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return data['result']
        except Exception as e:
            logger.error(f"Ошибка получения информации о фото {file_id}: {e}")
        
        return None








