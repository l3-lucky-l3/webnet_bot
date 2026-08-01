"""
Простой in-memory кэш для оптимизации производительности
Используется вместо Redis для простоты
"""
import time
from typing import Any, Optional, Dict
from threading import Lock
import logging

logger = logging.getLogger(__name__)


class SimpleCache:
    """Простой in-memory кэш с TTL"""
    
    def __init__(self):
        self._cache: Dict[str, tuple[Any, float]] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str, default: Any = None) -> Any:
        """Получить значение из кэша"""
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if expiry > time.time():
                    self._hits += 1
                    return value
                else:
                    # Истек срок действия
                    del self._cache[key]
            self._misses += 1
            return default
    
    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Установить значение в кэш с TTL (по умолчанию 5 минут)"""
        with self._lock:
            expiry = time.time() + ttl
            self._cache[key] = (value, expiry)
    
    def delete(self, key: str) -> None:
        """Удалить значение из кэша"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self) -> None:
        """Очистить весь кэш"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def cleanup_expired(self) -> None:
        """Удалить истекшие записи"""
        current_time = time.time()
        with self._lock:
            expired_keys = [
                key for key, (_, expiry) in self._cache.items()
                if expiry <= current_time
            ]
            for key in expired_keys:
                del self._cache[key]
        
        if expired_keys:
            logger.debug(f"Очищено {len(expired_keys)} истекших записей из кэша")
    
    def get_stats(self) -> Dict[str, int]:
        """Получить статистику кэша"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                'hits': self._hits,
                'misses': self._misses,
                'total': total,
                'hit_rate': round(hit_rate, 2),
                'size': len(self._cache)
            }


# Глобальный экземпляр кэша
cache = SimpleCache()

# Периодическая очистка истекших записей (каждые 10 минут)
def start_cache_cleanup():
    """Запустить периодическую очистку кэша"""
    import threading
    
    def cleanup_loop():
        while True:
            time.sleep(600)  # 10 минут
            cache.cleanup_expired()
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    logger.info("Запущена периодическая очистка кэша")

