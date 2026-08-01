"""
Система валидации входных данных
Защита от SQL injection, XSS, и других атак
"""
import re
import logging
from typing import Optional, Tuple
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class InputValidator:
    """Валидатор входных данных"""
    
    # Опасные паттерны
    SQL_INJECTION_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE|UNION)\b)",
        r"(--|#|/\*|\*/)",
        r"(\b(OR|AND)\s+\d+\s*=\s*\d+)",
        r"('|(\\')|(;)|(\|)|(\*)|(%)|(\[)|(\]))",
    ]
    
    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"<iframe[^>]*>.*?</iframe>",
        r"javascript:",
        r"on\w+\s*=",
        r"<img[^>]*onerror",
        r"<svg[^>]*onload",
    ]
    
    COMMAND_INJECTION_PATTERNS = [
        r"[;&|`$(){}[\]<>]",
        r"\.\./",
        r"\.\.\\",
        r"/etc/passwd",
        r"/proc/",
        r"cmd\.exe",
        r"powershell",
    ]
    
    @staticmethod
    def sanitize_string(value: str, max_length: int = 1000) -> str:
        """Очищает строку от опасных символов"""
        if not isinstance(value, str):
            value = str(value)
        
        # Обрезаем длину
        if len(value) > max_length:
            value = value[:max_length]
        
        # Удаляем нулевые байты
        value = value.replace('\x00', '')
        
        # Удаляем управляющие символы (кроме переносов строк и табуляции)
        value = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', value)
        
        return value.strip()
    
    @staticmethod
    def validate_email(email: str) -> Tuple[bool, Optional[str]]:
        """Валидирует email"""
        if not email or not isinstance(email, str):
            return False, "Email обязателен"
        
        if len(email) > 254:
            return False, "Email слишком длинный"
        
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return False, "Неверный формат email"
        
        return True, None
    
    @staticmethod
    def validate_user_id(user_id) -> Tuple[bool, Optional[str]]:
        """Валидирует user_id"""
        try:
            user_id = int(user_id)
            if user_id <= 0 or user_id > 2**63 - 1:  # Максимальный int64
                return False, "Неверный user_id"
            return True, None
        except (ValueError, TypeError):
            return False, "user_id должен быть числом"
    
    @staticmethod
    def validate_amount(amount) -> Tuple[bool, Optional[str], Optional[float]]:
        """Валидирует сумму платежа"""
        try:
            amount = float(amount)
            if amount <= 0:
                return False, "Сумма должна быть больше 0", None
            if amount > 1000000:  # Максимальная сумма
                return False, "Сумма слишком большая", None
            return True, None, amount
        except (ValueError, TypeError):
            return False, "Сумма должна быть числом", None
    
    @staticmethod
    def validate_subscription_type(sub_type: str) -> Tuple[bool, Optional[str]]:
        """Валидирует тип подписки"""
        allowed_types = ['month', '3months', '6months', 'year', 'trial', 'day',
                         'regular_day', 'regular_month', 'regular_3months', 'regular_6months', 'regular_year', 'regular_2years',
                         'fast_day', 'fast_month', 'fast_3months', 'fast_6months', 'fast_year']
        if sub_type not in allowed_types:
            return False, f"Неверный тип подписки. Допустимые: {', '.join(allowed_types)}"
        return True, None
    
    @staticmethod
    def validate_text(text: str, max_length: int = 4000, allow_html: bool = False) -> Tuple[bool, Optional[str]]:
        """Валидирует текст"""
        if not isinstance(text, str):
            return False, "Текст должен быть строкой"
        
        if len(text) > max_length:
            return False, f"Текст слишком длинный (максимум {max_length} символов)"
        
        # Проверяем на SQL injection
        text_lower = text.lower()
        for pattern in InputValidator.SQL_INJECTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                logger.warning(f"Обнаружен SQL injection паттерн в тексте: {pattern}")
                return False, "Обнаружен небезопасный контент"
        
        # Проверяем на XSS (если HTML не разрешен)
        if not allow_html:
            for pattern in InputValidator.XSS_PATTERNS:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    logger.warning(f"Обнаружен XSS паттерн в тексте: {pattern}")
                    return False, "Обнаружен небезопасный контент"
        
        return True, None
    
    @staticmethod
    def validate_payment_data(data: dict) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Валидирует данные платежа"""
        # Проверяем обязательные поля
        required_fields = ['user_id', 'subscription_type']
        for field in required_fields:
            if field not in data:
                return False, f"Отсутствует обязательное поле: {field}", None
        
        # Валидируем user_id
        is_valid, error = InputValidator.validate_user_id(data['user_id'])
        if not is_valid:
            return False, error, None
        
        # Валидируем subscription_type
        is_valid, error = InputValidator.validate_subscription_type(data['subscription_type'])
        if not is_valid:
            return False, error, None
        
        # Валидируем amount если есть
        if 'amount' in data and data['amount'] is not None:
            is_valid, error, amount = InputValidator.validate_amount(data['amount'])
            if not is_valid:
                return False, error, None
            data['amount'] = amount
        
        # Валидируем return_url если есть
        if 'return_url' in data and data['return_url']:
            is_valid, error = InputValidator.validate_text(data['return_url'], max_length=500)
            if not is_valid:
                return False, error, None
        
        return True, None, data
    
    @staticmethod
    def validate_json_request(request, required_fields: list = None) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Валидирует JSON запрос"""
        try:
            import json
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError) as e:
            return False, "Неверный формат JSON", None
        
        if not isinstance(data, dict):
            return False, "Данные должны быть объектом", None
        
        # Проверяем обязательные поля
        if required_fields:
            for field in required_fields:
                if field not in data:
                    return False, f"Отсутствует обязательное поле: {field}", None
        
        return True, None, data


# Глобальный экземпляр
input_validator = InputValidator()


def validate_input(validator_func):
    """Декоратор для валидации входных данных"""
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            is_valid, error, data = validator_func(request)
            if not is_valid:
                return JsonResponse({
                    'error': error or 'Ошибка валидации',
                    'code': 'VALIDATION_ERROR'
                }, status=400)
            
            # Добавляем валидированные данные в request
            if data:
                request.validated_data = data
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

