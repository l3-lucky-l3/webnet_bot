from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.utils import timezone
from django.db.models import Count, Sum, Q
import json
import logging
import csv
from io import StringIO

logger = logging.getLogger(__name__)

from .models import SubscriptionKey, Payment, TelegramUser
from .referral_models import Referral, ReferralCode, ReferralReward


@csrf_exempt
@require_http_methods(["GET"])
def get_keys_list_api(request):
    """API для получения списка ключей"""
    try:
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 20))
        subscription_type = request.GET.get('subscription_type')
        is_active = request.GET.get('is_active')
        
        keys_query = SubscriptionKey.objects.all().order_by('-key_id')
        
        if subscription_type:
            keys_query = keys_query.filter(subscription_type=subscription_type)
        
        if is_active is not None:
            is_active_bool = is_active.lower() == 'true'
            keys_query = keys_query.filter(is_active=is_active_bool)
        
        paginator = Paginator(keys_query, limit)
        page_obj = paginator.get_page(page)
        
        keys_list = []
        for key in page_obj:
            keys_list.append({
                'key_id': key.key_id,
                'key_value': key.key_value,
                'subscription_type': key.subscription_type,
                'total_activations': key.total_activations,
                'used_activations': key.used_activations,
                'remaining_activations': key.remaining_activations,
                'is_active': key.is_active,
                'is_available': key.is_available
            })
        
        return JsonResponse({
            'success': True,
            'keys': keys_list,
            'total': paginator.count,
            'page': page,
            'pages': paginator.num_pages
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения списка ключей: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def toggle_key_api(request):
    """API для включения/выключения ключа"""
    try:
        data = json.loads(request.body)
        key_id = data.get('key_id')
        is_active = data.get('is_active')
        
        if key_id is None or is_active is None:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно данных: нужны key_id и is_active'
            }, status=400)
        
        try:
            key = SubscriptionKey.objects.get(key_id=key_id)
            key.is_active = bool(is_active)
            key.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Ключ {"включен" if is_active else "выключен"}',
                'key': {
                    'key_id': key.key_id,
                    'is_active': key.is_active
                }
            })
        except SubscriptionKey.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Ключ не найден'
            }, status=404)
        
    except Exception as e:
        logger.error(f"Ошибка изменения статуса ключа: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_payments_list_api(request):
    """API для получения списка платежей"""
    try:
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 20))
        status_filter = request.GET.get('status')
        
        payments_query = Payment.objects.all().order_by('-payment_id')
        
        if status_filter:
            payments_query = payments_query.filter(status=status_filter)
        
        paginator = Paginator(payments_query, limit)
        page_obj = paginator.get_page(page)
        
        payments_list = []
        for payment in page_obj:
            payments_list.append({
                'payment_id': payment.payment_id,
                'user_id': payment.user.user_id,
                'username': payment.user.username,
                'first_name': payment.user.first_name,
                'amount': payment.amount,
                'status': payment.status,
                'subscription_type': payment.subscription_type,
                'issued_key': payment.issued_key,
                'created_at': payment.created_at.isoformat() if payment.created_at else None,
                'paid_at': payment.paid_at.isoformat() if payment.paid_at else None
            })
        
        return JsonResponse({
            'success': True,
            'payments': payments_list,
            'total': paginator.count,
            'page': page,
            'pages': paginator.num_pages
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения списка платежей: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_users_list_api(request):
    """API для получения списка пользователей"""
    try:
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 20))
        
        users_query = TelegramUser.objects.all().order_by('-created_at')
        
        paginator = Paginator(users_query, limit)
        page_obj = paginator.get_page(page)
        
        users_list = []
        for user in page_obj:
            users_list.append({
                'user_id': user.user_id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'balance': float(user.balance),
                'referral_balance': float(user.referral_balance),
                'created_at': user.created_at.isoformat() if user.created_at else None
            })
        
        return JsonResponse({
            'success': True,
            'users': users_list,
            'total': paginator.count,
            'page': page,
            'pages': paginator.num_pages
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def delete_key_api(request):
    """API для удаления ключа"""
    try:
        data = json.loads(request.body)
        key_id = data.get('key_id')
        
        if key_id is None:
            return JsonResponse({
                'success': False,
                'message': 'Не указан key_id'
            }, status=400)
        
        try:
            key = SubscriptionKey.objects.get(key_id=key_id)
            key_value = key.key_value
            key.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Ключ {key_value} удален'
            })
        except SubscriptionKey.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Ключ не найден'
            }, status=404)
        
    except Exception as e:
        logger.error(f"Ошибка удаления ключа: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_key_detail_api(request, key_id):
    """API для получения детальной информации о ключе"""
    try:
        key = SubscriptionKey.objects.get(key_id=key_id)
        
        return JsonResponse({
            'success': True,
            'key': {
                'key_id': key.key_id,
                'key_value': key.key_value,
                'subscription_type': key.subscription_type,
                'total_activations': key.total_activations,
                'used_activations': key.used_activations,
                'remaining_activations': key.remaining_activations,
                'is_active': key.is_active,
                'is_available': key.is_available
            }
        })
    except SubscriptionKey.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Ключ не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка получения ключа: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_payment_detail_api(request, payment_id):
    """API для получения детальной информации о платеже"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)

        return JsonResponse({
            'success': True,
            'payment': {
                'payment_id': payment.payment_id,
                'user_id': payment.user.user_id,
                'username': payment.user.username,
                'first_name': payment.user.first_name,
                'amount': payment.amount,
                'status': payment.status,
                'subscription_type': payment.subscription_type,
                'vpn_type': getattr(payment, 'vpn_type', 'night'),
                'issued_key': payment.issued_key,
                'created_at': payment.created_at.isoformat() if payment.created_at else None,
                'paid_at': payment.paid_at.isoformat() if payment.paid_at else None,
                'subscription_expires_at': payment.subscription_expires_at.isoformat() if payment.subscription_expires_at else None,
                'antilopay_recurrent_id': payment.antilopay_recurrent_id,
                'antilopay_payment_id': payment.antilopay_payment_id
            }
        })
    except Payment.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Платеж не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка получения платежа: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def confirm_payment_api(request):
    """API для подтверждения платежа с выдачей ключа"""
    try:
        data = json.loads(request.body)
        payment_id = data.get('payment_id')

        if payment_id is None:
            return JsonResponse({
                'success': False,
                'message': 'Не указан payment_id'
            }, status=400)

        try:
            payment = Payment.objects.get(payment_id=payment_id)

            if payment.status == 'succeeded':
                return JsonResponse({
                    'success': False,
                    'message': 'Платеж уже подтвержден'
                }, status=400)

            # Проверяем тип VPN и используем правильный сервис
            vpn_type = getattr(payment, 'vpn_type', 'night')
            
            if vpn_type == 'regular':
                # Для Обычного VPN используем Remnawave API
                from .platega_service import PlategaService
                success = PlategaService._handle_regular_vpn_payment_success(payment)
            elif vpn_type == 'fast':
                # Для Обычный VPN используем PaymentService
                from .services import PaymentService
                payment_service = PaymentService()
                success = payment_service.confirm_payment(payment)
            else:
                # Для Night VPN используем старый метод
                from .services import PaymentService
                payment_service = PaymentService()
                success = payment_service.confirm_payment(payment)

            if success:
                # Обновляем платеж из БД, чтобы получить актуальные данные
                payment.refresh_from_db()
                
                # Если дата окончания не установлена (для Обычного VPN), устанавливаем её
                if vpn_type == 'regular' and not payment.subscription_expires_at:
                    from datetime import timedelta
                    # Маппинг типов подписок на дни
                    duration_map = {
                        'regular_day': 1,
                        'regular_month': 30,
                        'regular_3months': 90,
                        'regular_6months': 180,
                        'regular_year': 365,
                        'regular_2years': 730
                    }
                    duration_days = duration_map.get(payment.subscription_type, 30)
                    payment.subscription_expires_at = timezone.now() + timedelta(days=duration_days)
                    payment.save()
                    logger.info(f"DEBUG: Установлена subscription_expires_at={payment.subscription_expires_at} для платежа {payment.payment_id}")

                # Ключ выдан — отправка сообщения пользователю происходит через бота (_send_key_message)
                # чтобы избежать дублирования уведомлений

                return JsonResponse({
                    'success': True,
                    'message': 'Платеж подтвержден и ключ выдан пользователю',
                    'payment': {
                        'payment_id': payment.payment_id,
                        'status': payment.status,
                        'issued_key': payment.issued_key
                    }
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'Ошибка при подтверждении платежа'
                }, status=500)

        except Payment.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Платеж не найден'
            }, status=404)
        
    except Exception as e:
        logger.error(f"Ошибка подтверждения платежа: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_referrers_list_api(request):
    """API для получения списка рефереров (тех, кто пригласил хотя бы 1 человека)"""
    try:
        page = int(request.GET.get('page', 1))
        limit = int(request.GET.get('limit', 20))
        search_query = request.GET.get('search', '').strip()
        # Убираем @ в начале для поиска по username (в БД username без @)
        search_clean = search_query.lstrip('@').strip() if search_query else ''

        # Получаем всех пользователей, которые пригласили хотя бы 1 человека
        referrers_query = TelegramUser.objects.filter(
            referrals_made__isnull=False
        ).annotate(
            referrals_count=Count('referrals_made', filter=Q(referrals_made__is_active=True))
        ).filter(
            referrals_count__gte=1
        ).distinct()

        # Поиск по username, first_name, last_name или user_id
        if search_clean:
            try:
                # Пробуем найти по user_id (только цифры)
                user_id_search = int(search_clean)
                referrers_query = referrers_query.filter(user_id=user_id_search)
            except ValueError:
                # Поиск по тексту (username без @, first_name, last_name)
                referrers_query = referrers_query.filter(
                    Q(username__icontains=search_clean) |
                    Q(first_name__icontains=search_clean) |
                    Q(last_name__icontains=search_clean)
                )
        
        # Сортируем по количеству рефералов (по убыванию)
        referrers_query = referrers_query.annotate(
            referrals_count=Count('referrals_made', filter=Q(referrals_made__is_active=True))
        ).order_by('-referrals_count', '-created_at')
        
        paginator = Paginator(referrers_query, limit)
        page_obj = paginator.get_page(page)
        
        referrers_list = []
        for referrer in page_obj:
            # Получаем количество покупок рефералов
            referred_user_ids = list(
                Referral.objects.filter(
                    referrer=referrer,
                    is_active=True
                ).values_list('referred_id', flat=True)
            )
            
            purchases_count = 0
            total_revenue = 0
            if referred_user_ids:
                purchases = Payment.objects.filter(
                    user_id__in=referred_user_ids,
                    status='succeeded'
                ).aggregate(
                    count=Count('payment_id'),
                    revenue=Sum('amount')
                )
                purchases_count = purchases['count'] or 0
                total_revenue = float(purchases['revenue'] or 0)
            
            # Получаем общую комиссию
            total_commission = ReferralReward.objects.filter(
                referral__referrer=referrer
            ).aggregate(total=Sum('reward_value'))['total'] or 0
            
            referrers_list.append({
                'user_id': referrer.user_id,
                'username': referrer.username,
                'first_name': referrer.first_name,
                'last_name': referrer.last_name,
                'referrals_count': referrer.referrals_count,
                'purchases_count': purchases_count,
                'total_revenue': total_revenue,
                'total_commission': float(total_commission),
                'referral_balance': float(referrer.referral_balance),
                'created_at': referrer.created_at.isoformat() if referrer.created_at else None,
                'has_purchases': purchases_count > 0  # Отмечаем, есть ли покупки
            })
        
        return JsonResponse({
            'success': True,
            'referrers': referrers_list,
            'total': paginator.count,
            'page': page,
            'pages': paginator.num_pages,
            'search': search_query
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения списка рефереров: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_referrer_detail_api(request, user_id):
    """API для получения детальной статистики реферала"""
    try:
        referrer = TelegramUser.objects.get(user_id=user_id)
        
        # Получаем всех рефералов
        referrals = Referral.objects.filter(
            referrer=referrer,
            is_active=True
        ).select_related('referred').order_by('-created_at')
        
        referred_user_ids = [ref.referred.user_id for ref in referrals]
        
        # Статистика покупок
        purchases_stats = Payment.objects.filter(
            user_id__in=referred_user_ids,
            status='succeeded'
        ).aggregate(
            total_purchases=Count('payment_id'),
            total_revenue=Sum('amount')
        )
        
        total_purchases = purchases_stats['total_purchases'] or 0
        total_revenue = float(purchases_stats['total_revenue'] or 0)
        
        # Общая комиссия
        total_commission = ReferralReward.objects.filter(
            referral__referrer=referrer
        ).aggregate(total=Sum('reward_value'))['total'] or 0
        
        # Детальная информация только о рефералах, которые купили что-то
        # (перешли по ссылке и совершили покупку)
        referrals_detail = []
        for ref in referrals:
            referred = ref.referred
            # Проверяем, есть ли покупки у этого реферала
            has_purchases = Payment.objects.filter(
                user_id=referred.user_id,
                status='succeeded'
            ).exists()
            
            # Показываем только тех, кто купил
            if not has_purchases:
                continue
            
            purchases_count = Payment.objects.filter(
                user_id=referred.user_id,
                status='succeeded'
            ).count()
            
            total_spent = Payment.objects.filter(
                user_id=referred.user_id,
                status='succeeded'
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            referrals_detail.append({
                'user_id': referred.user_id,
                'username': referred.username,
                'first_name': referred.first_name,
                'last_name': referred.last_name,
                'created_at': ref.created_at.isoformat() if ref.created_at else None,
                'has_purchases': True,  # Всегда True, так как фильтруем
                'purchases_count': purchases_count,
                'total_spent': float(total_spent)
            })
        
        # Реферальный код
        try:
            referral_code = ReferralCode.objects.get(user=referrer, is_active=True)
            code = referral_code.code
        except ReferralCode.DoesNotExist:
            code = None
        
        return JsonResponse({
            'success': True,
            'referrer': {
                'user_id': referrer.user_id,
                'username': referrer.username,
                'first_name': referrer.first_name,
                'last_name': referrer.last_name,
                'referral_code': code,
                'referral_balance': float(referrer.referral_balance),
                'created_at': referrer.created_at.isoformat() if referrer.created_at else None
            },
            'stats': {
                'referrals_count': referrals.count(),  # Всего рефералов
                'referrals_with_purchases_count': len(referrals_detail),  # Рефералов с покупками
                'total_purchases': total_purchases,
                'total_revenue': total_revenue,
                'total_commission': float(total_commission),
                'commission_percent': 20
            },
            'referrals': referrals_detail  # Только те, кто купил
        })
        
    except TelegramUser.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Реферер не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка получения детальной статистики реферала: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def export_referrer_referrals_api(request, user_id):
    """API для экспорта рефералов конкретного реферера (только тех, кто купил)"""
    try:
        referrer = TelegramUser.objects.get(user_id=user_id)
        
        # Получаем только рефералов, которые купили что-то
        referrals = Referral.objects.filter(
            referrer=referrer,
            is_active=True
        ).select_related('referred').order_by('-created_at')
        
        # Создаем CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow([
            'ID реферала',
            'Username',
            'Имя',
            'Фамилия',
            'Дата регистрации',
            'Количество покупок',
            'Общая сумма покупок',
            'Дата первой покупки'
        ])
        
        # Данные - только те, кто купил
        for ref in referrals:
            referred = ref.referred
            
            # Проверяем, есть ли покупки
            purchases = Payment.objects.filter(
                user_id=referred.user_id,
                status='succeeded'
            ).order_by('created_at')
            
            if not purchases.exists():
                continue  # Пропускаем тех, кто не купил
            
            purchases_count = purchases.count()
            total_spent = purchases.aggregate(total=Sum('amount'))['total'] or 0
            first_purchase_date = purchases.first().created_at if purchases.exists() else None
            
            writer.writerow([
                referred.user_id,
                referred.username or '',
                referred.first_name or '',
                referred.last_name or '',
                ref.created_at.strftime('%Y-%m-%d %H:%M:%S') if ref.created_at else '',
                purchases_count,
                f"{float(total_spent):.2f}",
                first_purchase_date.strftime('%Y-%m-%d %H:%M:%S') if first_purchase_date else ''
            ])
        
        # Возвращаем CSV файл
        referrer_name = referrer.username or referrer.first_name or f"ID{referrer.user_id}"
        filename = f"referrer_{referrer.user_id}_referrals_export.csv"
        response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except TelegramUser.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Реферер не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка экспорта рефералов реферера: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def export_referrers_api(request):
    """API для экспорта списка рефералов в CSV"""
    try:
        search_query = request.GET.get('search', '').strip()
        
        # Получаем всех рефереров
        referrers_query = TelegramUser.objects.filter(
            referrals_made__isnull=False
        ).annotate(
            referrals_count=Count('referrals_made', filter=Q(referrals_made__is_active=True))
        ).filter(
            referrals_count__gte=1
        ).distinct()
        
        # Поиск
        if search_query:
            try:
                user_id_search = int(search_query)
                referrers_query = referrers_query.filter(user_id=user_id_search)
            except ValueError:
                referrers_query = referrers_query.filter(
                    Q(username__icontains=search_query) |
                    Q(first_name__icontains=search_query) |
                    Q(last_name__icontains=search_query)
                )
        
        referrers_query = referrers_query.annotate(
            referrals_count=Count('referrals_made', filter=Q(referrals_made__is_active=True))
        ).order_by('-referrals_count', '-created_at')
        
        # Создаем CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow([
            'ID пользователя',
            'Username',
            'Имя',
            'Фамилия',
            'Количество рефералов',
            'Количество покупок',
            'Общая выручка',
            'Общая комиссия',
            'Реферальный баланс',
            'Дата регистрации',
                'Статус покупок'
        ])
        
        # Данные
        for referrer in referrers_query:
            referred_user_ids = list(
                Referral.objects.filter(
                    referrer=referrer,
                    is_active=True
                ).values_list('referred_id', flat=True)
            )
            
            purchases_count = 0
            total_revenue = 0
            if referred_user_ids:
                purchases = Payment.objects.filter(
                    user_id__in=referred_user_ids,
                    status='succeeded'
                ).aggregate(
                    count=Count('payment_id'),
                    revenue=Sum('amount')
                )
                purchases_count = purchases['count'] or 0
                total_revenue = float(purchases['revenue'] or 0)
            
            total_commission = ReferralReward.objects.filter(
                referral__referrer=referrer
            ).aggregate(total=Sum('reward_value'))['total'] or 0
            
            writer.writerow([
                referrer.user_id,
                referrer.username or '',
                referrer.first_name or '',
                referrer.last_name or '',
                referrer.referrals_count,
                purchases_count,
                f"{total_revenue:.2f}",
                f"{float(total_commission):.2f}",
                f"{float(referrer.referral_balance):.2f}",
                referrer.created_at.strftime('%Y-%m-%d %H:%M:%S') if referrer.created_at else '',
                'Есть покупки' if purchases_count > 0 else 'Нет покупок'
            ])
        
        # Возвращаем CSV файл
        response = HttpResponse(output.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="referrers_export.csv"'
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта рефереров: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)

