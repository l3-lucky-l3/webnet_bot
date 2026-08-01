from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import logging

logger = logging.getLogger(__name__)

from .models import BotSettings


@csrf_exempt
@require_http_methods(["GET"])
def get_setting_api(request):
    """API для получения настройки"""
    try:
        key = request.GET.get('key')
        if not key:
            return JsonResponse({
                'success': False,
                'message': 'Не указан ключ настройки'
            }, status=400)
        
        value = BotSettings.get_setting(key)
        return JsonResponse({
            'success': True,
            'key': key,
            'value': value
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения настройки: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def update_setting_api(request):
    """API для обновления настройки"""
    try:
        data = json.loads(request.body)
        key = data.get('key')
        value = data.get('value')
        
        if not key or value is None:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно данных: нужны key и value'
            }, status=400)
        
        BotSettings.set_setting(key, str(value))
        
        return JsonResponse({
            'success': True,
            'message': 'Настройка обновлена',
            'key': key,
            'value': value
        })
        
    except Exception as e:
        logger.error(f"Ошибка обновления настройки: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)



