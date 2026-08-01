from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import SubscriptionKey
import logging

logger = logging.getLogger(__name__)

@login_required
def edit_key_page(request, key_id):
    """Страница редактирования ключа"""
    key = get_object_or_404(SubscriptionKey, key_id=key_id)
    
    if request.method == 'POST':
        try:
            # Обновляем данные ключа
            key.subscription_type = request.POST.get('subscription_type', key.subscription_type)
            key.total_activations = int(request.POST.get('total_activations', key.total_activations))
            key.used_activations = int(request.POST.get('used_activations', key.used_activations))
            key.is_active = request.POST.get('is_active') == 'on'
            key.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Ключ успешно обновлен'
            })
            
        except Exception as e:
            logger.error(f"Ошибка обновления ключа {key_id}: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    
    # GET запрос - показываем форму
    context = {
        'key': key,
        'subscription_types': SubscriptionKey.SUBSCRIPTION_TYPES,
        'activation_choices': SubscriptionKey.ACTIVATION_CHOICES,
    }
    return render(request, 'bot_management/edit_key.html', context)


@login_required
def delete_key(request, key_id):
    """Удаление ключа"""
    if request.method == 'POST':
        try:
            key = get_object_or_404(SubscriptionKey, key_id=key_id)
            key.delete()
            
            return JsonResponse({
                'success': True,
                'message': 'Ключ успешно удален'
            })
            
        except Exception as e:
            logger.error(f"Ошибка удаления ключа {key_id}: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Только POST запросы'
        })
