from django.http import JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import SubscriptionKey
import logging

logger = logging.getLogger(__name__)

@login_required
def add_keys_page(request):
    """Страница для массового добавления ключей"""
    if request.method == 'POST':
        try:
            keys_text = request.POST.get('keys_text', '').strip()
            subscription_type = request.POST.get('subscription_type', 'month')
            if subscription_type not in ('trial', 'month'):
                return JsonResponse({
                    'success': False,
                    'message': 'Допустимые типы: только Пробная (1 день) и Месячная. Для 3 мес/год добавляйте месячные ключи.'
                })
            total_activations = int(request.POST.get('total_activations', 1))
            
            if not keys_text:
                return JsonResponse({
                    'success': False,
                    'message': 'Введите ключи для добавления'
                })
            
            # Разбиваем ключи по строкам
            keys_list = [key.strip() for key in keys_text.split('\n') if key.strip()]
            
            if not keys_list:
                return JsonResponse({
                    'success': False,
                    'message': 'Не найдено валидных ключей'
                })
            
            # Добавляем ключи
            added_count = 0
            duplicate_count = 0
            errors = []
            
            for key_value in keys_list:
                try:
                    key, created = SubscriptionKey.objects.get_or_create(
                        key_value=key_value,
                        defaults={
                            'subscription_type': subscription_type,
                            'total_activations': total_activations,
                            'used_activations': 0,
                            'is_active': True
                        }
                    )
                    if created:
                        added_count += 1
                    else:
                        duplicate_count += 1
                except Exception as e:
                    errors.append(f"Ошибка добавления ключа '{key_value}': {str(e)}")
            
            message = f"Добавлено: {added_count}, Дубликатов: {duplicate_count}"
            if errors:
                message += f", Ошибок: {len(errors)}"
            
            return JsonResponse({
                'success': True,
                'message': message,
                'added_count': added_count,
                'duplicate_count': duplicate_count,
                'errors': errors
            })
            
        except Exception as e:
            logger.error(f"Ошибка добавления ключей: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    
    # GET запрос - показываем форму (только месячные и пробные — общая база для 3м/год)
    subscription_types = [c for c in SubscriptionKey.SUBSCRIPTION_TYPES if c[0] in ('trial', 'month')]
    context = {
        'subscription_types': subscription_types,
        'activation_choices': SubscriptionKey.ACTIVATION_CHOICES,
    }
    return render(request, 'bot_management/add_keys.html', context)
