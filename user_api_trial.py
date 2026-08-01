"""
API для выдачи пробных ключей с разделением по типам VPN (Night VPN и Обычный VPN)
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import logging

from .models import TelegramUser, Payment

logger = logging.getLogger(__name__)


@csrf_exempt
def get_user_trial_status(request, user_id):
    """API для проверки статуса пробного ключа пользователя по VPN типу"""
    if request.method == 'GET':
        try:
            vpn_type = request.GET.get('vpn_type', 'night')  # night или regular
            
            user, created = TelegramUser.objects.get_or_create(
                user_id=user_id,
                defaults={
                    'username': None,
                    'first_name': None,
                    'last_name': None,
                    'balance': 0,
                    'referral_balance': 0,
                    'multi_level_referral_enabled': False,
                    'trial_key_used_night': False,
                    'trial_key_used_regular': False
                }
            )

            # Определяем, какой флаг проверять
            trial_used_field = f'trial_key_used_{vpn_type}'
            trial_used = getattr(user, trial_used_field, False)
            
            # Проверяем, есть ли активные пробные подписки для этого VPN
            active_trial = Payment.objects.filter(
                user=user,
                subscription_type='trial',
                vpn_type=vpn_type,
                status='succeeded',
                subscription_expires_at__gt=timezone.now()
            ).exists()

            return JsonResponse({
                'success': True,
                'trial_used': trial_used,
                'has_active_trial': active_trial,
                'vpn_type': vpn_type
            })

        except Exception as e:
            logger.error(f"Ошибка проверки статуса пробного ключа пользователя {user_id}: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Только GET запросы'
        })


@csrf_exempt
def issue_trial_key(request, user_id):
    """API для выдачи пробного ключа пользователю (с указанием типа VPN)"""
    if request.method == 'POST':
        try:
            from .services import PaymentService
            import json
            
            # Получаем тип VPN из запроса
            vpn_type = 'night'  # по умолчанию
            if request.content_type == 'application/json':
                data = json.loads(request.body)
                vpn_type = data.get('vpn_type', 'night')
            else:
                vpn_type = request.POST.get('vpn_type', 'night')
            
            # Проверяем тип VPN
            if vpn_type not in ['night', 'regular']:
                return JsonResponse({
                    'success': False,
                    'error': 'Неверный тип VPN. Используйте: night или regular'
                })

            user, created = TelegramUser.objects.get_or_create(
                user_id=user_id,
                defaults={
                    'username': None,
                    'first_name': None,
                    'last_name': None,
                    'balance': 0,
                    'referral_balance': 0,
                    'multi_level_referral_enabled': False,
                    'trial_key_used_night': False,
                    'trial_key_used_regular': False
                }
            )

            # Определяем, какой флаг проверять и обновлять
            trial_used_field = f'trial_key_used_{vpn_type}'
            
            # Проверяем, использовал ли пользователь пробный ключ для этого VPN
            if getattr(user, trial_used_field, False):
                return JsonResponse({
                    'success': False,
                    'error': f'Пробный ключ для {vpn_type} VPN уже был использован'
                })

            # Проверяем, есть ли активные пробные подписки для этого VPN
            active_trial = Payment.objects.filter(
                user=user,
                subscription_type='trial',
                vpn_type=vpn_type,
                status='succeeded',
                subscription_expires_at__gt=timezone.now()
            ).exists()

            if active_trial:
                return JsonResponse({
                    'success': False,
                    'error': f'У вас уже есть активная пробная подписка для {vpn_type} VPN'
                })

            # Создаем платеж для пробного ключа со статусом pending
            # Для regular VPN используем subscription_type='regular_trial'
            payment_subscription_type = 'regular_trial' if vpn_type == 'regular' else 'trial'
            payment = Payment.objects.create(
                user=user,
                amount=0,  # Бесплатно
                status='pending',
                subscription_type=payment_subscription_type,
                vpn_type=vpn_type,
            )

            # Для Night VPN с Remnawave Bypass API — выдаём ключ напрямую
            if vpn_type == 'night':
                from config import REMNAWAVE_BYPASS_API_KEY
                if REMNAWAVE_BYPASS_API_KEY:
                    from .night_vpn_fgn_service import process_night_vpn_payment_sync
                    
                    try:
                        result = process_night_vpn_payment_sync(payment.payment_id)
                        logger.info(f"Remnawave Bypass trial result: {result}")
                        
                        if result and result.get('success'):
                            key_value = result.get('key_value')
                            payment.refresh_from_db()
                            
                            setattr(user, trial_used_field, True)
                            user.save(update_fields=[trial_used_field])
                            
                            expires_str = payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M') if payment.subscription_expires_at else '—'
                            
                            return JsonResponse({
                                'success': True,
                                'issued_key': key_value,
                                'expires_at': expires_str,
                                'vpn_type': vpn_type,
                                'vpn_label': 'Night VPN'
                            })
                        else:
                            error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Пустой ответ'
                            logger.warning(f"Remnawave Bypass trial ошибка: {error_msg}, пробуем пул ключей")
                    except Exception as e:
                        logger.warning(f"Remnawave Bypass trial ошибка: {e}, пробуем пул ключей")

            # Fallback: выдаем ключ через PaymentService (пул ключей)
            payment_service = PaymentService()
            success = payment_service.confirm_payment(payment)

            if success:
                # Обновляем данные платежа из БД (для regular VPN ключ выдаётся через Remnawave API)
                payment.refresh_from_db()
                
                # Помечаем, что пользователь использовал пробный ключ для этого VPN
                setattr(user, trial_used_field, True)
                user.save(update_fields=[trial_used_field])

                expires_at = payment.subscription_expires_at
                if expires_at:
                    expires_str = expires_at.strftime('%d.%m.%Y %H:%M')
                else:
                    expires_str = 'неизвестно'

                vpn_label = 'Обычный VPN' if vpn_type == 'regular' else 'Night VPN'
                return JsonResponse({
                    'success': True,
                    'issued_key': payment.issued_key,
                    'expires_at': expires_str,
                    'vpn_type': vpn_type,
                    'vpn_label': vpn_label
                })
            else:
                # Удаляем созданный платеж при ошибке
                payment.delete()
                return JsonResponse({
                    'success': False,
                    'error': 'Не удалось выдать пробный ключ'
                })

        except Exception as e:
            logger.error(f"Ошибка выдачи пробного ключа пользователю {user_id}: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Только POST запросы'
        })
