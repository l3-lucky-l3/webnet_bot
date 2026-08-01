from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import SupportChat
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def delete_support_chat(request, chat_id):
    """Удаление чата поддержки"""
    if request.method == 'POST':
        try:
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            # Удаляем все сообщения чата
            chat.messages.all().delete()
            
            # Удаляем сам чат
            chat.delete()
            
            return JsonResponse({
                'success': True,
                'message': 'Чат успешно удален'
            })
            
        except SupportChat.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Чат не найден'
            })
        except Exception as e:
            logger.error(f"Ошибка удаления чата {chat_id}: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка удаления: {str(e)}'
            })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Только POST запросы'
        })
