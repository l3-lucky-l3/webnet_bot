from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, Count, Q
from .models import TelegramUser, Payment, SubscriptionKey
import logging

logger = logging.getLogger(__name__)


@csrf_exempt
def health_check(request):
    """Health check endpoint для проверки доступности Django API"""
    return JsonResponse({
        'status': 'ok',
        'message': 'Django API is running'
    })


@csrf_exempt
def get_user_keys(request, user_id):
    """API для получения ключей пользователя"""
    if request.method == 'GET':
        try:
            # Получаем или создаем пользователя с инициализацией полей
            user, created = TelegramUser.objects.get_or_create(
                user_id=user_id,
                defaults={
                    'username': None,
                    'first_name': None,
                    'last_name': None,
                    'balance': 0,
                    'referral_balance': 0,
                    'multi_level_referral_enabled': False
                }
            )
            
            # Получаем все платежи пользователя со статусом 'succeeded'
            # Исключаем те, что истекли более 30 дней назад
            # Включаем платежи с пустым issued_key (ожидают выдачи менеджером)
            # Оптимизация: получаем только нужные поля
            cutoff_date = timezone.now() - timedelta(days=30)
            total_active_payments = Payment.objects.filter(user=user, status='succeeded').count()
            payments = Payment.objects.filter(
                user=user,
                status='succeeded'
            ).filter(
                Q(subscription_expires_at__gte=cutoff_date) | Q(subscription_expires_at__isnull=True)
            ).only('payment_id', 'issued_key', 'subscription_type', 'paid_at', 'created_at').order_by('-created_at')
            hidden_expired = total_active_payments - payments.count()
            
            # Получаем все ключи одним запросом для оптимизации
            issued_keys = [p.issued_key for p in payments if p.issued_key]
            keys_dict = {}
            if issued_keys:
                keys_dict = {
                    key.key_value: key 
                    for key in SubscriptionKey.objects.filter(key_value__in=issued_keys).only(
                        'key_value', 'subscription_type', 'total_activations', 
                        'used_activations', 'is_active'
                    )
                }
            
            keys_data = []
            for payment in payments:
                if payment.issued_key:
                    # Ищем ключ в кэше
                    key = keys_dict.get(payment.issued_key)
                    if key:
                        keys_data.append({
                            'key_value': key.key_value,
                            'subscription_type': key.subscription_type,
                            'vpn_type': payment.vpn_type if payment.vpn_type else (getattr(key, 'vpn_type', 'night') or 'night'),
                            'total_activations': key.total_activations,
                            'used_activations': key.used_activations,
                            'is_active': key.is_active,
                            'payment_id': payment.payment_id,
                            'created_at': payment.paid_at.isoformat() if payment.paid_at else payment.created_at.isoformat(),
                            'issued_by_manager': False  # Ключ из базы, не выдан менеджером
                        })
                    else:
                        # Ключ не найден в базе, но есть в платеже - значит выдан менеджером
                        keys_data.append({
                            'key_value': payment.issued_key,
                            'subscription_type': payment.subscription_type,
                            'vpn_type': getattr(payment, 'vpn_type', 'night') or 'night',
                            'total_activations': 1,  # По умолчанию
                            'used_activations': 0,
                            'is_active': True,
                            'payment_id': payment.payment_id,
                            'created_at': payment.paid_at.isoformat() if payment.paid_at else payment.created_at.isoformat(),
                            'issued_by_manager': True  # Флаг что ключ выдан менеджером
                        })
                else:
                    # Платеж подтвержден, но ключа нет в базе - значит выдан менеджером в личке
                    # Менеджер мог просто отправить ключ пользователю напрямую, не добавляя в систему
                    keys_data.append({
                        'key_value': f"Выдан менеджером (Платеж #{payment.payment_id})",
                        'subscription_type': payment.subscription_type,
                        'total_activations': 1,
                        'used_activations': 0,
                        'is_active': True,  # Считаем активным, так как ключ уже выдан
                        'payment_id': payment.payment_id,
                        'created_at': payment.paid_at.isoformat() if payment.paid_at else payment.created_at.isoformat(),
                        'issued_by_manager': True,  # Выдан менеджером
                        'pending_manager': False  # Уже выдан
                    })
            
            return JsonResponse({
                'success': True,
                'keys': keys_data,
                'count': len(keys_data),
                'hidden_expired_count': hidden_expired if hidden_expired > 0 else 0,
            })
            
        except TelegramUser.DoesNotExist:
            # Если пользователь не найден, возвращаем пустой список ключей
            return JsonResponse({
                'success': True,
                'keys': [],
                'count': 0
            })
        except Exception as e:
            logger.error(f"Ошибка получения ключей пользователя {user_id}: {e}")
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
def get_user_trial_status(request, user_id):
    """API для проверки статуса пробного ключа пользователя"""
    if request.method == 'GET':
        try:
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

            # Проверяем, есть ли активные пробные подписки
            active_trial = Payment.objects.filter(
                user=user,
                subscription_type='trial',
                status='succeeded',
                subscription_expires_at__gt=timezone.now()
            ).exists()

            return JsonResponse({
                'success': True,
                'trial_used': user.trial_key_used_night,
                'has_active_trial': active_trial
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
    """API для выдачи пробного ключа пользователю"""
    if request.method == 'POST':
        try:
            from .services import PaymentService
            from datetime import timedelta

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

            # Проверяем, использовал ли пользователь пробный ключ Night VPN
            if user.trial_key_used_night:
                return JsonResponse({
                    'success': False,
                    'error': 'Пробный ключ уже был использован'
                })

            # Проверяем, есть ли активные пробные подписки
            active_trial = Payment.objects.filter(
                user=user,
                subscription_type='trial',
                status='succeeded',
                subscription_expires_at__gt=timezone.now()
            ).exists()

            if active_trial:
                return JsonResponse({
                    'success': False,
                    'error': 'У вас уже есть активная пробная подписка'
                })

            # Создаем платеж для пробного ключа со статусом pending
            payment = Payment.objects.create(
                user=user,
                amount=0,  # Бесплатно
                status='pending',
                subscription_type='trial',
            )

            # Выдаем ключ через PaymentService
            payment_service = PaymentService()
            success = payment_service.confirm_payment(payment)

            if success:
                # Помечаем, что пользователь использовал пробный ключ Night VPN
                user.trial_key_used_night = True
                user.save(update_fields=['trial_key_used_night'])

                expires_at = payment.subscription_expires_at
                if expires_at:
                    expires_str = expires_at.strftime('%d.%m.%Y %H:%M')
                else:
                    expires_str = 'неизвестно'

                response_data = {
                    'success': True,
                    'issued_key': payment.issued_key,
                    'expires_at': expires_str
                }
                if payment.regular_vpn_key:
                    response_data['regular_vpn_key'] = payment.regular_vpn_key
                
                return JsonResponse(response_data)
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


@csrf_exempt
def reset_subscription_devices(request, payment_id):
    """API для сброса всех устройств подписки (HWID)"""
    if request.method == 'POST':
        try:
            from .models import Payment
            import asyncio

            payment = Payment.objects.select_related('user').get(payment_id=payment_id)

            if payment.status != 'succeeded' or not payment.issued_key:
                return JsonResponse({'success': False, 'error': 'Подписка не активна'})

            user_uuid = _get_user_uuid(payment)
            if not user_uuid:
                return JsonResponse({'success': False, 'error': 'Не найден UUID пользователя в Remnawave'})

            client = _get_remnawave_client(payment.vpn_type)
            if not client:
                return JsonResponse({'success': False, 'error': 'API Remnawave недоступен'})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(client.delete_all_user_devices(user_uuid))
            finally:
                loop.close()

            if success:
                logger.info(f"Устройства сброшены для подписки {payment_id} (uuid={user_uuid})")
                return JsonResponse({'success': True, 'message': 'Все устройства успешно сброшены'})
            else:
                return JsonResponse({'success': False, 'error': 'Ошибка при сбросе устройств'})

        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Подписка не найдена'})
        except Exception as e:
            logger.error(f"Ошибка сброса устройств для подписки {payment_id}: {e}")
            return JsonResponse({'success': False, 'message': str(e)})
    else:
        return JsonResponse({'success': False, 'message': 'Только POST запросы'})


@csrf_exempt
def get_subscription_devices(request, payment_id):
    """API для получения списка HWID устройств подписки"""
    if request.method == 'GET':
        try:
            from .models import Payment, SubscriptionKey
            from .remnawave_api import get_remnawave_client, get_remnawave_bypass_client, get_remnawave_fast_vpn_client
            import asyncio

            payment = Payment.objects.select_related('user').get(payment_id=payment_id)

            user_uuid = _get_user_uuid(payment)
            if not user_uuid:
                return JsonResponse({'success': False, 'error': 'Не найден UUID пользователя'})

            client = _get_remnawave_client(payment.vpn_type)
            if not client:
                return JsonResponse({'success': False, 'error': 'API Remnawave недоступен'})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                devices = loop.run_until_complete(client.get_user_devices(user_uuid))
            finally:
                loop.close()

            if devices is not None:
                return JsonResponse({'success': True, 'devices': devices, 'count': len(devices)})
            else:
                return JsonResponse({'success': False, 'error': 'Ошибка получения устройств'})

        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Подписка не найдена'})
        except Exception as e:
            logger.error(f"Ошибка получения устройств для подписки {payment_id}: {e}")
            return JsonResponse({'success': False, 'message': str(e)})
    else:
        return JsonResponse({'success': False, 'message': 'Только GET запросы'})


@csrf_exempt
def delete_subscription_device(request, payment_id):
    """API для удаления конкретного HWID устройства подписки"""
    if request.method == 'POST':
        try:
            import json
            from .models import Payment
            from .remnawave_api import get_remnawave_client, get_remnawave_bypass_client, get_remnawave_fast_vpn_client
            import asyncio

            data = json.loads(request.body)
            hwid = data.get('hwid')
            if not hwid:
                return JsonResponse({'success': False, 'error': 'Не указан HWID устройства'})

            payment = Payment.objects.select_related('user').get(payment_id=payment_id)

            user_uuid = _get_user_uuid(payment)
            if not user_uuid:
                return JsonResponse({'success': False, 'error': 'Не найден UUID пользователя'})

            client = _get_remnawave_client(payment.vpn_type)
            if not client:
                return JsonResponse({'success': False, 'error': 'API Remnawave недоступен'})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(client.delete_user_device(user_uuid, hwid))
            finally:
                loop.close()

            if success:
                logger.info(f"Устройство {hwid} удалено из подписки {payment_id}")
                return JsonResponse({'success': True, 'message': 'Устройство удалено'})
            else:
                return JsonResponse({'success': False, 'error': 'Ошибка удаления устройства'})

        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Подписка не найдена'})
        except Exception as e:
            logger.error(f"Ошибка удаления устройства из подписки {payment_id}: {e}")
            return JsonResponse({'success': False, 'message': str(e)})
    else:
        return JsonResponse({'success': False, 'message': 'Только POST запросы'})


def _get_user_uuid(payment):
    """Получить UUID пользователя в Remnawave из платежа"""
    from .models import SubscriptionKey
    sub_key = SubscriptionKey.objects.filter(key_value=payment.issued_key).first()
    if sub_key and sub_key.remnawave_user_uuid:
        return sub_key.remnawave_user_uuid
    vpn_type = payment.vpn_type or 'night'
    if vpn_type == 'night' and payment.bypass_remnawave_uuid:
        return payment.bypass_remnawave_uuid
    if vpn_type == 'regular' and payment.regular_vpn_remnawave_uuid:
        return payment.regular_vpn_remnawave_uuid
    return None


def _get_remnawave_client(vpn_type):
    """Получить Remnawave клиент по типу VPN"""
    from .remnawave_api import get_remnawave_client, get_remnawave_bypass_client, get_remnawave_fast_vpn_client
    vpn_type = vpn_type or 'night'
    if vpn_type == 'regular':
        return get_remnawave_client()
    elif vpn_type == 'fast':
        return get_remnawave_fast_vpn_client()
    return get_remnawave_bypass_client()


@csrf_exempt
def get_payment_stats_today(request):
    """API для получения статистики платежей за сегодня"""
    if request.method == 'GET':
        try:
            # Определяем дату для анализа (сегодня)
            target_date = timezone.now().date()

            # Получаем начало и конец дня
            start_of_day = timezone.datetime.combine(target_date, timezone.datetime.min.time())
            end_of_day = timezone.datetime.combine(target_date, timezone.datetime.max.time())

            # Фильтруем платежи за сегодня
            payments_today = Payment.objects.filter(
                Q(paid_at__date=target_date) |  # Оплаченные сегодня
                Q(status='succeeded', paid_at__isnull=True, created_at__date=target_date)  # Успешные без paid_at
            ).exclude(status='canceled')  # Исключаем отмененные

            # Общая статистика
            total_payments = payments_today.count()
            total_amount = payments_today.filter(status='succeeded').aggregate(
                total=Sum('amount')
            )['total'] or 0

            # Статистика по статусам
            status_stats = payments_today.values('status').annotate(
                count=Count('status'),
                amount=Sum('amount')
            ).order_by('-count')

            # Статистика по типам подписок
            subscription_stats = payments_today.filter(status='succeeded').values('subscription_type').annotate(
                count=Count('subscription_type'),
                amount=Sum('amount')
            ).order_by('-amount')

            # Конвертируем статусы в читаемый формат
            status_data = {}
            status_names = {
                'succeeded': 'оплаченные',
                'pending': 'ожидают_оплаты',
                'failed': 'ошибки_оплаты',
                'canceled': 'отмененные'
            }

            for stat in status_stats:
                status_key = status_names.get(stat['status'], stat['status'])
                status_data[status_key] = {
                    'count': stat['count'],
                    'amount': stat['amount'] or 0
                }

            # Конвертируем типы подписок в читаемый формат
            subscription_data = {}
            subscription_names = {
                'trial': 'пробная',
                'week': 'недельная',
                'month': 'месячная',
                '3months': '3_месяца',
                '6months': '6_месяцев',
                'year': 'годовая'
            }

            for stat in subscription_stats:
                sub_key = subscription_names.get(stat['subscription_type'], stat['subscription_type'])
                subscription_data[sub_key] = {
                    'count': stat['count'],
                    'amount': stat['amount'] or 0
                }

            # Сравнение с предыдущим днем
            yesterday = target_date - timezone.timedelta(days=1)
            yesterday_payments = Payment.objects.filter(
                Q(paid_at__date=yesterday) |
                Q(status='succeeded', paid_at__isnull=True, created_at__date=yesterday)
            ).exclude(status='canceled')

            yesterday_amount = yesterday_payments.filter(status='succeeded').aggregate(
                total=Sum('amount')
            )['total'] or 0

            comparison = None
            if yesterday_amount > 0:
                change = total_amount - yesterday_amount
                change_percent = (change / yesterday_amount) * 100 if yesterday_amount > 0 else 0
                comparison = {
                    'yesterday_amount': yesterday_amount,
                    'change_amount': change,
                    'change_percent': round(change_percent, 1)
                }

            return JsonResponse({
                'success': True,
                'date': target_date.isoformat(),
                'total_payments': total_payments,
                'total_amount': total_amount,
                'by_status': status_data,
                'by_subscription_type': subscription_data,
                'comparison_with_yesterday': comparison
            })

        except Exception as e:
            logger.error(f"Ошибка получения статистики платежей: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка: {str(e)}'
            })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Только GET запросы'
        })
