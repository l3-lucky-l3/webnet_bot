"""
Продвинутая система защиты от DDoS атак
Включает IP блокировку, rate limiting, обнаружение аномалий
"""
import time
import logging
import ipaddress
from collections import defaultdict
from functools import wraps
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


class DDoSProtection:
    """Система защиты от DDoS атак"""
    
    def __init__(self):
        # Лимиты для разных типов запросов
        self.rate_limits = {
            'api_general': {'requests': 100, 'window': 60},  # 100 запросов в минуту
            'api_payment': {'requests': 10, 'window': 60},  # 10 запросов в минуту
            'api_auth': {'requests': 5, 'window': 60},  # 5 попыток в минуту
            'webhook': {'requests': 50, 'window': 60},  # 50 webhook'ов в минуту
            'admin': {'requests': 200, 'window': 60},  # 200 запросов в минуту для админов
        }
        
        # Заблокированные IP адреса
        self.blocked_ips = set()
        
        # Подозрительные IP (для мониторинга)
        self.suspicious_ips = defaultdict(list)
        
        # Паттерны атак
        self.attack_patterns = [
            'sql injection', 'union select', 'drop table', 'delete from',
            'script>', '<iframe', 'javascript:', 'onerror=', 'onload=',
            '../', '..\\', '/etc/passwd', 'cmd=', 'exec=', 'eval(',
        ]
        
        # IP диапазоны для блокировки (VPN/Proxy)
        self.blocked_ranges = [
            # Можно добавить известные VPN/Proxy диапазоны
        ]
    
    def get_client_ip(self, request):
        """Получает реальный IP адрес клиента"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip
    
    def is_ip_blocked(self, ip):
        """Проверяет, заблокирован ли IP"""
        # Проверяем в памяти
        if ip in self.blocked_ips:
            return True
        
        # Проверяем в кеше
        if cache.get(f"blocked_ip_{ip}"):
            return True
        
        # Проверяем диапазоны
        try:
            ip_obj = ipaddress.ip_address(ip)
            for range_str in self.blocked_ranges:
                if ip_obj in ipaddress.ip_network(range_str, strict=False):
                    return True
        except (ValueError, ipaddress.AddressValueError):
            pass
        
        return False
    
    def block_ip(self, ip, duration=3600, reason="DDoS attack detected"):
        """Блокирует IP адрес"""
        self.blocked_ips.add(ip)
        cache.set(f"blocked_ip_{ip}", True, timeout=duration)
        logger.warning(f"IP {ip} заблокирован на {duration} секунд. Причина: {reason}")
    
    def check_rate_limit(self, ip, limit_type='api_general'):
        """Проверяет rate limit для IP"""
        if self.is_ip_blocked(ip):
            return False, "IP заблокирован"
        
        limit_config = self.rate_limits.get(limit_type, self.rate_limits['api_general'])
        max_requests = limit_config['requests']
        window = limit_config['window']
        
        cache_key = f"ratelimit_{limit_type}_{ip}"
        current_time = time.time()
        
        # Получаем историю запросов
        requests = cache.get(cache_key, [])
        # Фильтруем старые запросы
        requests = [req_time for req_time in requests if current_time - req_time < window]
        
        # Проверяем лимит
        if len(requests) >= max_requests:
            # Превышен лимит - блокируем IP
            self.block_ip(ip, duration=300, reason=f"Rate limit exceeded: {limit_type}")
            self.record_suspicious_activity(ip, f"Rate limit exceeded: {limit_type}")
            return False, f"Превышен лимит запросов: {max_requests}/{window}с"
        
        # Добавляем текущий запрос
        requests.append(current_time)
        cache.set(cache_key, requests, timeout=window)
        
        return True, None
    
    def detect_attack_pattern(self, request):
        """Обнаруживает паттерны атак в запросах"""
        # Проверяем URL
        url = request.path.lower()
        query_string = request.GET.urlencode().lower()
        body = ''
        
        if request.method == 'POST':
            try:
                body = request.body.decode('utf-8', errors='ignore').lower()
            except:
                pass
        
        # Проверяем все паттерны
        all_text = f"{url} {query_string} {body}"
        for pattern in self.attack_patterns:
            if pattern in all_text:
                ip = self.get_client_ip(request)
                self.record_suspicious_activity(ip, f"Attack pattern detected: {pattern}")
                logger.warning(f"Обнаружен паттерн атаки '{pattern}' от IP {ip}")
                return True, pattern
        
        return False, None
    
    def record_suspicious_activity(self, ip, reason):
        """Записывает подозрительную активность"""
        current_time = time.time()
        self.suspicious_ips[ip].append({
            'time': current_time,
            'reason': reason
        })
        
        # Оставляем только последние 10 событий
        if len(self.suspicious_ips[ip]) > 10:
            self.suspicious_ips[ip] = self.suspicious_ips[ip][-10:]
        
        # Если за последние 5 минут было 5+ подозрительных событий - блокируем
        recent_events = [
            event for event in self.suspicious_ips[ip]
            if current_time - event['time'] < 300
        ]
        
        if len(recent_events) >= 5:
            self.block_ip(ip, duration=1800, reason=f"Multiple suspicious activities: {reason}")
    
    def validate_request(self, request):
        """Валидирует запрос на безопасность"""
        ip = self.get_client_ip(request)
        
        # Проверяем блокировку IP
        if self.is_ip_blocked(ip):
            return False, "IP заблокирован", 403
        
        # Проверяем паттерны атак
        is_attack, pattern = self.detect_attack_pattern(request)
        if is_attack:
            self.block_ip(ip, duration=3600, reason=f"Attack pattern: {pattern}")
            return False, "Обнаружена попытка атаки", 403
        
        return True, None, None


# Глобальный экземпляр
ddos_protection = DDoSProtection()


def ddos_protect(limit_type='api_general', require_auth=False):
    """
    Декоратор для защиты от DDoS
    
    Args:
        limit_type: Тип лимита ('api_general', 'api_payment', 'api_auth', 'webhook', 'admin')
        require_auth: Требовать ли аутентификацию
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Получаем IP
            ip = ddos_protection.get_client_ip(request)
            
            # Проверяем блокировку
            if ddos_protection.is_ip_blocked(ip):
                logger.warning(f"Заблокированный IP {ip} пытается получить доступ к {request.path}")
                return JsonResponse({
                    'error': 'Доступ запрещен',
                    'code': 'IP_BLOCKED'
                }, status=403)
            
            # Проверяем rate limit
            allowed, error = ddos_protection.check_rate_limit(ip, limit_type)
            if not allowed:
                logger.warning(f"Rate limit превышен для IP {ip} на {request.path}")
                return JsonResponse({
                    'error': 'Превышен лимит запросов. Попробуйте позже.',
                    'code': 'RATE_LIMIT_EXCEEDED'
                }, status=429)
            
            # Валидируем запрос
            is_valid, error, status_code = ddos_protection.validate_request(request)
            if not is_valid:
                return JsonResponse({
                    'error': error or 'Небезопасный запрос',
                    'code': 'REQUEST_BLOCKED'
                }, status=status_code or 403)
            
            # Проверяем аутентификацию если требуется
            if require_auth and not request.user.is_authenticated:
                return JsonResponse({
                    'error': 'Требуется аутентификация',
                    'code': 'AUTH_REQUIRED'
                }, status=401)
            
            # Выполняем view
            try:
                return view_func(request, *args, **kwargs)
            except Exception as e:
                logger.error(f"Ошибка в защищенном view {view_func.__name__}: {e}")
                # Не раскрываем детали ошибки в production
                return JsonResponse({
                    'error': 'Внутренняя ошибка сервера',
                    'code': 'INTERNAL_ERROR'
                }, status=500)
        
        return wrapper
    return decorator


def get_ddos_stats():
    """Получает статистику DDoS защиты"""
    return {
        'blocked_ips_count': len(ddos_protection.blocked_ips),
        'suspicious_ips_count': len(ddos_protection.suspicious_ips),
        'rate_limits': ddos_protection.rate_limits,
    }

