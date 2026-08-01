from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import TelegramUser, SupportChat, SupportMessage
from .services import SupportService
from config import BOT_TOKEN
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
def receive_support_message(request):
    """Получение сообщения от пользователя в чат поддержки"""
    logger.info(f"receive_support_message called with method: {request.method}")
    if request.method == 'POST':
        try:
            user_id = request.POST.get('user_id')
            message = request.POST.get('message')
            
            if not user_id or not message:
                return JsonResponse({'status': 'error', 'message': 'Неверные параметры'})
            
            # Получаем или создаем пользователя
            user, created = TelegramUser.objects.get_or_create(
                user_id=user_id,
                defaults={
                    'username': f'user_{user_id}',
                    'first_name': 'User',
                    'last_name': user_id,
                    'multi_level_referral_enabled': False
                }
            )
            
            # Создаем или получаем чат
            chat, created = SupportChat.objects.get_or_create(
                user=user,
                status='open',
                defaults={'status': 'open'}
            )
            
            # Создаем сообщение
            SupportMessage.objects.create(
                chat=chat,
                sender='user',
                text=message
            )
            
            # Обновляем счетчик непрочитанных сообщений для админа
            chat.unread_admin_messages += 1
            chat.save()
            
            # Уведомления админам убраны - только в группу
            
            return JsonResponse({
                'status': 'success', 
                'message': 'Сообщение получено',
                'chat_id': chat.chat_id
            })
                
        except Exception as e:
            logger.error(f"Ошибка получения сообщения поддержки: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)})
    else:
        return JsonResponse({'status': 'error', 'message': 'Только POST запросы'})
