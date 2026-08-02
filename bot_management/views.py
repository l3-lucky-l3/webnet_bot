from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum, F
from django.utils import timezone
from datetime import timedelta
import json
import logging

logger = logging.getLogger(__name__)
import asyncio
# DDoS защита убрана - только для бота

# Импорт планировщика уведомлений
try:
    from notification_scheduler import get_scheduler_status
except ImportError:
    logger.warning("notification_scheduler не найден, API статус недоступен")
    get_scheduler_status = lambda: {"error": "scheduler_not_available"}

from .models import (
    TelegramUser, SubscriptionKey, Payment, 
    SupportChat, SupportMessage, Broadcast, BotSettings,
    PromoCode, PromoCodeUsage,
)
from .services import PaymentService, BroadcastService, SupportService
from .receive_support import receive_support_message
from .delete_support import delete_support_chat
from .add_keys_view import add_keys_page
from .edit_key_view import edit_key_page, delete_key
from .user_api import get_user_keys
from .views_api_lists import (
    get_keys_list_api, toggle_key_api,
    get_payments_list_api, get_users_list_api,
    delete_key_api, get_key_detail_api,
    get_payment_detail_api, confirm_payment_api,
    get_referrers_list_api, get_referrer_detail_api, 
    export_referrer_referrals_api, export_referrers_api
)
from .views_settings import (
    get_setting_api, update_setting_api
)

logger = logging.getLogger(__name__)


def get_price(subscription_type: str) -> int:
    """
    Получает цену подписки из BotSettings или из config.py как fallback

    Args:
        subscription_type: Тип подписки ('month', '3months', '6months', 'year')

    Returns:
        Цена в рублях
    """
    # Сначала проверяем BotSettings
    price_setting = BotSettings.get_setting(f'price_{subscription_type}')
    if price_setting:
        try:
            return int(price_setting)
        except (ValueError, TypeError):
            pass

    # Если нет в BotSettings, используем config.py
    try:
        import sys
        import os
        bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, bot_dir)
        from config import PRICES
        return PRICES.get(subscription_type, 0)
    except ImportError:
        # Fallback цены
        PRICES = {
            'month': 390,
            '3months': 990,
            '6months': 1890,
            'year': 3900
        }
        return PRICES.get(subscription_type, 0)


@login_required
def dashboard(request):
    """Главная страница админки"""
    # Статистика пользователей
    total_users = TelegramUser.objects.count()
    new_users_today = TelegramUser.objects.filter(
        created_at__date=timezone.now().date()
    ).count()
    
    # Статистика платежей
    total_payments = Payment.objects.count()
    pending_payments = Payment.objects.filter(status='pending').count()
    succeeded_payments = Payment.objects.filter(status='succeeded').count()
    total_revenue = Payment.objects.filter(status='succeeded').aggregate(
        total=Sum('amount')
    )['total'] or 0

    total_profit = Payment.objects.filter(status='succeeded').aggregate(
        total=Sum('profit')
    )['total'] or 0

    ultra_fast_profit = Payment.objects.filter(status='succeeded', vpn_type='regular').aggregate(
        total=Sum('profit')
    )['total'] or 0

    fast_vpn_profit = Payment.objects.filter(status='succeeded', vpn_type='fast').aggregate(
        total=Sum('profit')
    )['total'] or 0

    night_vpn_profit = Payment.objects.filter(status='succeeded', vpn_type='night').aggregate(
        total=Sum('profit')
    )['total'] or 0

    # Выручка за сегодня (по аналогии с payment_stats_today)
    today = timezone.now().date()
    from django.db.models import Q
    payments_today = Payment.objects.filter(
        Q(paid_at__date=today) |
        Q(status='succeeded', paid_at__isnull=True, created_at__date=today)
    ).exclude(status='canceled')
    today_revenue = payments_today.filter(status='succeeded').aggregate(
        total=Sum('amount')
    )['total'] or 0
    
    
    # Статистика ключей
    total_keys = SubscriptionKey.objects.count()
    available_keys = SubscriptionKey.objects.filter(
        is_active=True
    ).count()
    
    # Статистика поддержки
    total_support_chats = SupportChat.objects.count()
    active_support_chats = SupportChat.objects.filter(status='open').count()
    unread_messages = SupportMessage.objects.filter(is_read=False).count()

    context = {
        'total_users': total_users,
        'new_users_today': new_users_today,
        'total_payments': total_payments,
        'pending_payments': pending_payments,
        'succeeded_payments': succeeded_payments,
        'total_revenue': total_revenue,
        'total_profit': total_profit,
        'ultra_fast_profit': ultra_fast_profit,
        'fast_vpn_profit': fast_vpn_profit,
        'night_vpn_profit': night_vpn_profit,
        'today_revenue': today_revenue,
        'total_keys': total_keys,
        'available_keys': available_keys,
        'total_support_chats': total_support_chats,
        'active_support_chats': active_support_chats,
        'unread_messages': unread_messages,
    }
    
    return render(request, 'bot_management/dashboard.html', context)


@login_required
def users_list(request):
    """Список пользователей"""
    users = TelegramUser.objects.all().order_by('-created_at')
    return render(request, 'bot_management/users_list.html', {'users': users})


@login_required
def payments_list(request):
    """Список платежей"""
    payments = Payment.objects.all().order_by('-created_at')
    return render(request, 'bot_management/payments_list.html', {'payments': payments})


@login_required
def keys_list(request):
    """Список ключей"""
    keys = SubscriptionKey.objects.all().order_by('-key_id')
    return render(request, 'bot_management/keys_list.html', {'keys': keys})


@csrf_exempt
def statistics_api(request):
    """API для получения статистики"""
    try:
        # Статистика пользователей
        total_users = TelegramUser.objects.count()
        new_users_today = TelegramUser.objects.filter(
            created_at__date=timezone.now().date()
        ).count()
        new_users_week = TelegramUser.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=7)
        ).count()
        
        # Статистика платежей
        total_payments = Payment.objects.count()
        pending_payments = Payment.objects.filter(status='pending').count()
        succeeded_payments = Payment.objects.filter(status='succeeded').count()
        total_revenue = Payment.objects.filter(status='succeeded').aggregate(
            total=Sum('amount')
        )['total'] or 0

        total_profit = Payment.objects.filter(status='succeeded').aggregate(
            total=Sum('profit')
        )['total'] or 0

        ultra_fast_profit = Payment.objects.filter(status='succeeded', vpn_type='regular').aggregate(
            total=Sum('profit')
        )['total'] or 0

        fast_vpn_profit = Payment.objects.filter(status='succeeded', vpn_type='fast').aggregate(
            total=Sum('profit')
        )['total'] or 0

        night_vpn_profit = Payment.objects.filter(status='succeeded', vpn_type='night').aggregate(
            total=Sum('profit')
        )['total'] or 0

        today = timezone.now().date()
        payments_today = Payment.objects.filter(
            Q(paid_at__date=today) |
            Q(status='succeeded', paid_at__isnull=True, created_at__date=today)
        ).exclude(status='canceled')
        revenue_today = payments_today.filter(status='succeeded').aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # Статистика ключей
        total_keys = SubscriptionKey.objects.count()
        available_keys = SubscriptionKey.objects.filter(is_active=True).count()
        
        return JsonResponse({
            'users': {
                'total': total_users,
                'new_today': new_users_today,
                'new_week': new_users_week
            },
            'payments': {
                'total': total_payments,
                'pending': pending_payments,
                'succeeded': succeeded_payments,
                'revenue': float(total_revenue),
                'total_profit': total_profit,
                'ultra_fast_profit': ultra_fast_profit,
                'fast_vpn_profit': fast_vpn_profit,
                'night_vpn_profit': night_vpn_profit,
                'revenue_today': float(revenue_today)
            },
            'keys': {
                'total': total_keys,
                'available': available_keys
            }
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def create_payment(request):
    """API для создания платежа"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        subscription_type = data.get('subscription_type')
        return_url = data.get('return_url')
        payment_method = data.get('payment_method', 2)  # По умолчанию СБП (QR)
        vpn_type = data.get('vpn_type', 'night')  # По умолчанию Night VPN
        is_renewal = data.get('is_renewal', False)  # Флаг продления
        renewal_for_payment_id = data.get('renewal_for_payment_id')  # ID платежа который продлевается

        if not all([user_id, subscription_type]):
            return JsonResponse({'status': 'error', 'message': 'Недостаточно данных'}, status=400)

        # Если amount не передан, определяем по subscription_type
        if amount is None:
            amount = get_price(subscription_type)

        # Получаем или создаем пользователя
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={'username': f'user_{user_id}'}
        )

        # Определяем оригинальный платеж для продления
        renewal_for_payment = None
        if is_renewal and renewal_for_payment_id:
            try:
                renewal_for_payment = Payment.objects.get(payment_id=renewal_for_payment_id, user=user)
            except Payment.DoesNotExist:
                logger.warning(f"Оригинальный платеж {renewal_for_payment_id} не найден для пользователя {user_id}")

        # Создаем платеж
        payment = Payment.objects.create(
            user=user,
            amount=amount,
            subscription_type=subscription_type,
            status='pending',
            vpn_type=vpn_type,
            is_renewal=is_renewal,
            renewal_for_payment=renewal_for_payment,
        )

        # Для платежей, созданных через админку, напоминание тоже не нужно
        # так как они обрабатываются вручную
        
        # Создаем платеж в Platega
        from .platega_service import PlategaService
        from config import PLATEGA_MERCHANT_ID, PLATEGA_SECRET
        
        # Проверяем credentials
        if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
            logger.error("PLATEGA_MERCHANT_ID или PLATEGA_SECRET не установлены")
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': 'Платежная система не настроена. Обратитесь к администратору.'
            }, status=500)
        
        # Формируем return_url для бота
        if not return_url:
            bot_username = "webnetvpn_robot"
            return_url = f"https://t.me/{bot_username}?start=payment_success_{payment.payment_id}"
            failed_url = f"https://t.me/{bot_username}?start=payment_failed_{payment.payment_id}"
        else:
            failed_url = return_url.replace('success', 'failed')
        
        logger.info(f"Создание платежа Platega: payment_id={payment.payment_id}, amount={amount}, return_url={return_url}")
        
        try:
            platega_data = PlategaService.create_payment(payment, return_url=return_url, failed_url=failed_url, payment_method=payment_method)
        except Exception as e:
            logger.error(f"Исключение при создании платежа Platega: {e}")
            import traceback
            logger.error(traceback.format_exc())
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': f'Ошибка создания платежа: {str(e)}'
            }, status=500)
        
        if platega_data:
            logger.info(f"Платеж Platega успешно создан: transaction_id={platega_data.get('transactionId')}")
            return JsonResponse({
                'status': 'success',
                'payment_id': payment.payment_id,
                'transaction_id': platega_data.get('transactionId'),
                'confirmation_url': platega_data.get('redirect'),
                'amount': float(payment.amount),
                'subscription_type': subscription_type
            })
        else:
            # Если не удалось создать в Platega, возвращаем ошибку
            logger.error(f"Не удалось создать платеж Platega для payment_id={payment.payment_id}")
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': 'Ошибка создания платежа в Platega. Проверьте логи сервера.'
            }, status=500)
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def create_cryptobot_payment(request):
    """API для создания платежа через CryptoBot"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        subscription_type = data.get('subscription_type')
        asset = data.get('asset', 'USDT')  # По умолчанию USDT
        vpn_type = data.get('vpn_type', 'night')  # По умолчанию Night VPN
        is_renewal = data.get('is_renewal', False)
        renewal_for_payment_id = data.get('renewal_for_payment_id')

        if not all([user_id, subscription_type]):
            return JsonResponse({'status': 'error', 'message': 'Недостаточно данных'}, status=400)

        # Если amount не передан, определяем по subscription_type
        if amount is None:
            amount = get_price(subscription_type)
            # Если get_price вернул 0 для regular_* подписок, используем REGULAR_VPN_PRICES
            if amount == 0 and subscription_type.startswith('regular_'):
                from config import REGULAR_VPN_PRICES
                regular_type = subscription_type.replace('regular_', '')
                amount = REGULAR_VPN_PRICES.get(regular_type, 0)

        # Проверяем что CryptoBot настроен
        from config import CRYPTOBOT_API_TOKEN
        if not CRYPTOBOT_API_TOKEN or len(CRYPTOBOT_API_TOKEN.strip()) == 0:
            logger.error("CRYPTOBOT_API_TOKEN не установлен!")
            return JsonResponse({
                'status': 'error',
                'message': 'Оплата через CryptoBot временно недоступна. Обратитесь в поддержку.'
            }, status=500)

        # Получаем или создаем пользователя
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={'username': f'user_{user_id}'}
        )

        # Определяем оригинальный платеж для продления
        renewal_for_payment = None
        if is_renewal and renewal_for_payment_id:
            try:
                renewal_for_payment = Payment.objects.get(payment_id=renewal_for_payment_id, user=user)
            except Payment.DoesNotExist:
                logger.warning(f"Оригинальный платеж {renewal_for_payment_id} не найден для пользователя {user_id}")

        # Создаем платеж
        payment = Payment.objects.create(
            user=user,
            amount=amount,
            subscription_type=subscription_type,
            status='pending',
            vpn_type=vpn_type,
            is_renewal=is_renewal,
            renewal_for_payment=renewal_for_payment,
        )

        logger.info(f"Создание платежа CryptoBot: payment_id={payment.payment_id}, amount={amount}, asset={asset}")

        # Создаем invoice напрямую через requests (синхронно)
        import requests as requests_lib

        # Определяем описание
        subscription_names = {
            'week': '1 неделя (ОБХОД глушилок + VPN)',
            'month': 'Месячная подписка',
            '3months': '3 месяца',
            '6months': '6 месяцев',
            'year': 'Годовая подписка',
            'trial': 'Пробная подписка',
            'regular_day': '1 день (ULTRA FAST VPN)',
            'regular_month': '1 месяц (ULTRA FAST VPN)',
            'regular_3months': '3 месяца (ULTRA FAST VPN)',
            'regular_6months': '6 месяцев (ULTRA FAST VPN)',
            'regular_year': '1 год (ULTRA FAST VPN)',
            'regular_2years': '2 года (ULTRA FAST VPN)',
            'fast_week': '1 неделя (Обычный VPN)',
            'fast_month': '1 месяц (Обычный VPN)',
            'fast_3months': '3 месяца (Обычный VPN)',
            'fast_6months': '6 месяцев (Обычный VPN)',
            'fast_year': '1 год (Обычный VPN)',
        }
        vpn_label = "ULTRA FAST VPN" if vpn_type == 'regular' else ("Обычный VPN" if vpn_type == 'fast' else "Night VPN")
        sub_name = subscription_names.get(subscription_type, subscription_type)
        description = f"{vpn_label}: {sub_name}"

        # CryptoBot API endpoint
        api_url = 'https://pay.crypt.bot/api/createInvoice'

        # Получаем курс конвертации RUB -> нужная валюта
        # CryptoBot поддерживает только криптовалюты, RUB нужно конвертировать
        # Сумма в RUB
        rub_amount = float(amount)
        crypto_amount = rub_amount  # По умолчанию если не удалось конвертировать

        try:
            # Пробуем получить курс через внешний API (exchangerate-api)
            rates_url = f'https://api.exchangerate-api.com/v4/latest/RUB'
            rates_response = requests_lib.get(rates_url, timeout=15)

            if rates_response.status_code == 200:
                rates_data = rates_response.json()
                target_asset = asset.upper()

                # Маппинг криптовалют на коды из exchangerate-api
                asset_to_currency = {
                    'USDT': 'USD',   # USDT ~ 1 USD
                    'TON': 'USD',
                    'BTC': 'USD',
                    'ETH': 'USD',
                    'LTC': 'USD',
                    'TRX': 'USD',
                }

                usd_currency_code = asset_to_currency.get(target_asset, 'USD')
                usd_rate = rates_data.get('rates', {}).get(usd_currency_code)

                if usd_rate:
                    # Конвертируем RUB -> USD (для USDT это примерно 1:1)
                    usd_amount = rub_amount * usd_rate

                    # Теперь конвертируем USD -> crypto через CryptoBot курсы
                    cb_rates_url = 'https://pay.crypt.bot/api/getExchangeRates'
                    cb_rates_headers = {
                        'Crypto-Pay-API-Token': CRYPTOBOT_API_TOKEN,
                    }
                    cb_rates_response = requests_lib.get(cb_rates_url, headers=cb_rates_headers, timeout=15)

                    if cb_rates_response.status_code == 200:
                        cb_data = cb_rates_response.json()
                        if cb_data.get('ok'):
                            cb_rates = cb_data.get('result', [])
                            # Ищем курс USD -> target_asset
                            rate = None
                            for r in cb_rates:
                                if r.get('source') == 'USD' and r.get('target') == target_asset:
                                    rate = float(r.get('rate', 1))
                                    break

                            if rate:
                                crypto_amount = round(usd_amount * rate, 6)
                                logger.info(f"Курс: {rub_amount} RUB -> {usd_amount:.2f} USD -> {crypto_amount} {target_asset}")
                            else:
                                # Fallback: считаем что 1 USD = 1 USDT
                                crypto_amount = round(usd_amount, 2)
                                logger.info(f"Fallback курс: {rub_amount} RUB = {crypto_amount} {target_asset} (через USD)")
                        else:
                            crypto_amount = round(usd_amount, 2)
                            logger.info(f"Fallback: {rub_amount} RUB = {crypto_amount} {target_asset}")
                    else:
                        crypto_amount = round(usd_amount, 2)
                        logger.info(f"Fallback: {rub_amount} RUB = {crypto_amount} {target_asset}")
                else:
                    logger.warning(f"Не найден курс RUB -> {usd_currency_code}")
            else:
                logger.warning(f"Не удалось получить курсы: {rates_response.status_code}")
        except Exception as e:
            logger.warning(f"Ошибка конвертации валют: {e}")

        payload = {
            'asset': asset.upper(),
            'amount': crypto_amount,
            'description': description,
            'payload': str(payment.payment_id),  # В payload кладем ID платежа
            'bot_invoice_url': True,  # Включить invoice в боте
        }

        headers_create = {
            'Content-Type': 'application/json',
            'Crypto-Pay-API-Token': CRYPTOBOT_API_TOKEN,
        }

        logger.info(f"Запрос к CryptoBot API: {api_url}, RUB={rub_amount} -> {crypto_amount} {asset.upper()}, payload={payload}")

        response = requests_lib.post(api_url, json=payload, headers=headers_create, timeout=30)

        logger.info(f"CryptoBot API response: {response.status_code} - {response.text}")

        if response.status_code == 200:
            result = response.json()

            if result.get('ok'):
                invoice = result.get('result', {})
                invoice_id = invoice.get('invoice_id')
                payment_url = invoice.get('bot_invoice_url') or invoice.get('mini_app_invoice_url') or invoice.get('invoice_url')

                if not payment_url:
                    logger.error(f"Не удалось получить URL для invoice {invoice_id}")
                    payment.status = 'failed'
                    payment.save()
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Ошибка получения ссылки на оплату'
                    }, status=500)

                # Обновляем модель платежа
                payment.cryptobot_invoice_id = str(invoice_id)
                payment.cryptobot_payment_url = payment_url
                payment.cryptobot_asset = asset.upper()
                payment.save()

                logger.info(f"CryptoBot invoice создан: {invoice_id}, URL: {payment_url}")

                return JsonResponse({
                    'status': 'success',
                    'payment_id': payment.payment_id,
                    'invoice_id': str(invoice_id),
                    'confirmation_url': payment_url,
                    'amount': float(invoice.get('amount', amount)),
                    'asset': asset.upper(),
                    'subscription_type': subscription_type
                })
            else:
                error_msg = result.get('error', {}).get('message', 'Неизвестная ошибка')
                logger.error(f"CryptoBot API ошибка: {error_msg}")
                payment.status = 'failed'
                payment.save()
                return JsonResponse({
                    'status': 'error',
                    'message': f'Ошибка CryptoBot: {error_msg}'
                }, status=500)
        else:
            logger.error(f"CryptoBot HTTP ошибка: {response.status_code} - {response.text}")
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': f'HTTP ошибка: {response.status_code}'
            }, status=500)

    except Exception as e:
        logger.error(f"Ошибка создания платежа CryptoBot: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_payment_status(request, payment_id):
    """API для получения статуса платежа"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)
        return JsonResponse({
            'payment_id': payment.payment_id,
            'status': payment.status,
            'amount': float(payment.amount),
            'created_at': payment.created_at.isoformat(),
            'platega_transaction_id': payment.platega_transaction_id,
            'cryptobot_invoice_id': payment.cryptobot_invoice_id,
            'antilopay_payment_id': payment.antilopay_payment_id,
            'antilopay_payment_url': payment.antilopay_payment_url,
            'antilopay_recurrent_id': payment.antilopay_recurrent_id,
            'cryptobot_asset': payment.cryptobot_asset,
            'issued_key': payment.issued_key,
            'vpn_type': getattr(payment, 'vpn_type', 'night'),
            'subscription_type': payment.subscription_type,
        })
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Платеж не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def check_platega_payment_status(request, payment_id):
    """API для проверки статуса платежа через Platega API по payment_id"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)
        
        if not payment.platega_transaction_id:
            return JsonResponse({
                'success': False,
                'message': 'У платежа нет platega_transaction_id'
            }, status=400)
        
        # Проверяем статус через Platega API по transaction_id
        from .platega_service import PlategaService
        platega_status = PlategaService.get_payment_status(payment.platega_transaction_id)
        
        if not platega_status:
            return JsonResponse({
                'success': False,
                'message': 'Не удалось получить статус от Platega'
            }, status=500)
        
        platega_status_value = platega_status.get('status', 'unknown')
        
        # Маппинг статусов Platega (согласно документации: PENDING, CANCELED, CONFIRMED, CHARGEBACKED)
        platega_status_normalized = platega_status_value.upper() if platega_status_value else 'UNKNOWN'
        
        # Если платеж успешен в Platega (CONFIRMED), но еще не подтвержден у нас - подтверждаем
        if platega_status_normalized == 'CONFIRMED' and payment.status != 'succeeded':
            logger.info(f"DEBUG: Платеж {payment.payment_id} успешен в Platega ({platega_status_normalized}), подтверждаем")
            
            # Проверяем тип VPN - для Обычного VPN используем Remnawave API
            subscription_type = payment.subscription_type
            vpn_type = getattr(payment, 'vpn_type', 'night')
            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')
            
            if is_regular_vpn:
                logger.info(f"DEBUG: Платеж {payment.payment_id} - ULTRA FAST VPN, обрабатываем через Remnawave API")
                from .platega_service import PlategaService
                webhook_data = {
                    'id': payment.platega_transaction_id,
                    'status': 'CONFIRMED',
                    'amount': payment.amount,
                    'currency': 'RUB',
                    'paymentMethod': 2,
                    'payload': ''
                }
                PlategaService.process_webhook(webhook_data, skip_notification=True)
            elif is_fast_vpn:
                logger.info(f"DEBUG: Платеж {payment.payment_id} - Обычный VPN, обрабатываем через bypass API")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)
            else:
                # Для Night VPN используем старый метод
                logger.info(f"DEBUG: Платеж {payment.payment_id} - Night VPN, обрабатываем через PaymentService")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)
            
            payment.refresh_from_db()
            logger.info(f"DEBUG: Платеж {payment.payment_id} подтвержден, ключ выдан: {payment.issued_key}")
        
        return JsonResponse({
            'success': True,
            'platega_status': platega_status_value,
            'payment_status': payment.status,
            'payment_id': payment.payment_id,
            'platega_transaction_id': payment.platega_transaction_id,
            'issued_key': payment.issued_key if payment.status == 'succeeded' else None,
            'vpn_type': getattr(payment, 'vpn_type', 'night'),
            'subscription_type': payment.subscription_type,
        })
        
    except Payment.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Платеж не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка проверки статуса платежа через Platega: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def check_platega_payment_by_transaction_id(request, transaction_id):
    """API для проверки и подтверждения платежа через Platega API по transaction_id (GET запрос)"""
    try:
        # Находим платеж по transaction_id
        payment = Payment.objects.filter(platega_transaction_id=transaction_id).first()
        
        if not payment:
            return JsonResponse({
                'success': False,
                'message': f'Платеж с transaction_id {transaction_id} не найден'
            }, status=404)
        
        logger.info(f"DEBUG: Проверка платежа по transaction_id {transaction_id}, payment_id={payment.payment_id}")
        
        # Проверяем статус через Platega API
        from .platega_service import PlategaService
        platega_status = PlategaService.get_payment_status(transaction_id)
        
        if not platega_status:
            return JsonResponse({
                'success': False,
                'message': 'Не удалось получить статус от Platega'
            }, status=500)
        
        platega_status_value = platega_status.get('status', 'unknown')
        
        # Маппинг статусов Platega (согласно документации: PENDING, CANCELED, CONFIRMED, CHARGEBACKED)
        platega_status_normalized = platega_status_value.upper() if platega_status_value else 'UNKNOWN'
        
        # Если платеж успешен в Platega (CONFIRMED), но еще не подтвержден у нас - подтверждаем
        if platega_status_normalized == 'CONFIRMED' and payment.status != 'succeeded':
            logger.info(f"DEBUG: Платеж {payment.payment_id} (transaction_id={transaction_id}) успешен в Platega ({platega_status_normalized}), подтверждаем")
            
            # Проверяем тип VPN - для Обычного VPN используем Remnawave API
            subscription_type = payment.subscription_type
            vpn_type = getattr(payment, 'vpn_type', 'night')
            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')
            
            if is_regular_vpn:
                logger.info(f"DEBUG: Платеж {payment.payment_id} - ULTRA FAST VPN, обрабатываем через Remnawave API")
                from .platega_service import PlategaService
                webhook_data = {
                    'id': transaction_id,
                    'status': 'CONFIRMED',
                    'amount': payment.amount,
                    'currency': 'RUB',
                    'paymentMethod': 2,
                    'payload': ''
                }
                PlategaService.process_webhook(webhook_data, skip_notification=True)
            elif is_fast_vpn:
                logger.info(f"DEBUG: Платеж {payment.payment_id} - Обычный VPN, обрабатываем через bypass API")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)
            else:
                # Для Night VPN используем старый метод
                logger.info(f"DEBUG: Платеж {payment.payment_id} - Night VPN, обрабатываем через PaymentService")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)
            
            payment.refresh_from_db()
            logger.info(f"DEBUG: Платеж {payment.payment_id} подтвержден, ключ выдан: {payment.issued_key}")
        elif payment.status == 'succeeded':
            logger.info(f"DEBUG: Платеж {payment.payment_id} уже был подтвержден ранее")
        
        return JsonResponse({
            'success': True,
            'platega_status': platega_status_value,
            'payment_status': payment.status,
            'payment_id': payment.payment_id,
            'platega_transaction_id': payment.platega_transaction_id,
            'issued_key': payment.issued_key if payment.status == 'succeeded' else None,
            'amount': float(payment.amount),
            'subscription_type': payment.subscription_type
        })
        
    except Exception as e:
        logger.error(f"Ошибка проверки платежа по transaction_id {transaction_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def check_cryptobot_payment_status(request, payment_id):
    """API для проверки статуса платежа через CryptoBot по payment_id"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)

        if not payment.cryptobot_invoice_id:
            return JsonResponse({
                'success': False,
                'message': 'У платежа нет cryptobot_invoice_id'
            }, status=400)

        from config import CRYPTOBOT_API_TOKEN
        import requests as requests_lib

        # CryptoBot API: getInvoices принимает invoice_ids в query params
        cb_api_url = f'https://pay.crypt.bot/api/getInvoices?invoice_ids={payment.cryptobot_invoice_id}'
        cb_headers = {
            'Crypto-Pay-API-Token': CRYPTOBOT_API_TOKEN,
        }

        logger.info(f"CryptoBot проверка: payment_id={payment_id}, invoice_id={payment.cryptobot_invoice_id}, URL={cb_api_url}")

        response = requests_lib.get(cb_api_url, headers=cb_headers, timeout=30)

        logger.info(f"CryptoBot getInvoices response: {response.status_code} - {response.text[:500]}")

        if response.status_code != 200:
            return JsonResponse({
                'success': False,
                'message': f'HTTP ошибка: {response.status_code} - {response.text[:200]}'
            }, status=500)

        result = response.json()
        logger.info(f"CryptoBot getInvoices parsed result: {str(result)[:500]}")

        if not result.get('ok'):
            return JsonResponse({
                'success': False,
                'message': result.get('error', {}).get('message', 'Ошибка CryptoBot')
            }, status=500)

        invoices_raw = result.get('result', [])
        logger.info(f"CryptoBot invoices_raw type={type(invoices_raw).__name__}")
        
        # CryptoBot может вернуть разные форматы:
        # 1. list: [...]
        # 2. dict с ключом 'items': {"items": [...]}
        # 3. dict один invoice: {"invoice_id": ...}
        if isinstance(invoices_raw, dict):
            if 'items' in invoices_raw:
                items = invoices_raw.get('items', [])
                if not items:
                    return JsonResponse({
                        'success': False,
                        'message': 'Invoice не найден'
                    }, status=404)
                invoice = items[0]
            else:
                # Один invoice как dict
                invoice = invoices_raw
        elif isinstance(invoices_raw, list):
            if not invoices_raw:
                return JsonResponse({
                    'success': False,
                    'message': 'Invoice не найден'
                }, status=404)
            invoice = invoices_raw[0]
        else:
            return JsonResponse({
                'success': False,
                'message': f'Неожиданный формат ответа: {type(invoices_raw)}'
            }, status=500)

        logger.info(f"CryptoBot invoice данные: {invoice}")
        cryptobot_status_value = invoice.get('status', 'unknown')  # active, paid, expired

        # Нормализуем статус
        cryptobot_status_normalized = cryptobot_status_value.lower() if cryptobot_status_value else 'unknown'

        # Если платеж успешен в CryptoBot (paid), но еще не подтвержден у нас - подтверждаем
        if cryptobot_status_normalized == 'paid' and payment.status != 'succeeded':
            logger.info(f"Платеж {payment.payment_id} успешен в CryptoBot ({cryptobot_status_normalized}), подтверждаем")

            # Проверяем тип VPN
            subscription_type = payment.subscription_type
            vpn_type = getattr(payment, 'vpn_type', 'night')
            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')

            if is_regular_vpn:
                logger.info(f"Платеж {payment.payment_id} - ULTRA FAST VPN, обрабатываем через Remnawave API")
                from .cryptobot_service import CryptobotService
                CryptobotService._handle_regular_vpn_payment_success(payment, skip_notification=True)
            elif is_fast_vpn:
                logger.info(f"Платеж {payment.payment_id} - Обычный VPN, обрабатываем через bypass API")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)
            else:
                # Для Night VPN используем старый метод
                logger.info(f"Платеж {payment.payment_id} - Night VPN, обрабатываем через PaymentService")
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment)

            payment.refresh_from_db()
            logger.info(f"Платеж {payment.payment_id} подтвержден, ключ выдан: {payment.issued_key}")

        return JsonResponse({
            'success': True,
            'cryptobot_status': cryptobot_status_value,
            'payment_status': payment.status,
            'payment_id': payment.payment_id,
            'cryptobot_invoice_id': payment.cryptobot_invoice_id,
            'issued_key': payment.issued_key if payment.status == 'succeeded' else None,
            'amount': float(payment.amount),
            'subscription_type': payment.subscription_type,
            'vpn_type': getattr(payment, 'vpn_type', 'night'),
        })

    except Payment.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Платеж не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка проверки платежа {payment_id} через CryptoBot: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {type(e).__name__}: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def manual_confirm_payment(request, payment_id):
    """API для ручного подтверждения платежа с выдачей ключа"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)

        if payment.status == 'succeeded' and payment.issued_key:
            return JsonResponse({
                'success': True,
                'message': 'Платеж уже подтвержден',
                'issued_key': payment.issued_key
            })

        is_renewal = getattr(payment, 'is_renewal', False) and payment.renewal_for_payment

        if is_renewal:
            logger.info(f"Платеж {payment_id} — продление подписки, обрабатываем через PaymentService")
            from .services import PaymentService
            payment_service = PaymentService()
            success = payment_service._confirm_renewal_payment(payment)
            payment.refresh_from_db()
            if success:
                return JsonResponse({
                    'success': True,
                    'message': 'Продление подтверждено, ключ продлён',
                    'issued_key': payment.issued_key
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': 'Не удалось продлить подписку'
                })

        subscription_type = payment.subscription_type
        vpn_type = getattr(payment, 'vpn_type', 'night')
        is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
        is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')

        if is_regular_vpn:
            from .regular_vpn_service import process_regular_vpn_payment_success_sync
            result = process_regular_vpn_payment_success_sync(payment.payment_id)
            if result and result.get('success'):
                return JsonResponse({
                    'success': True,
                    'message': 'Платеж подтвержден, ключ выдан',
                    'issued_key': result.get('key')
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': result.get('error', 'Ошибка генерации ключа') if result else 'Ошибка генерации ключа'
                })

        elif is_fast_vpn:
            from .night_vpn_fgn_service import process_fast_vpn_payment_sync
            result = process_fast_vpn_payment_sync(payment.payment_id)
            payment.refresh_from_db()
            if result and result.get('success'):
                return JsonResponse({
                    'success': True,
                    'message': 'Платеж подтвержден, ключ выдан',
                    'issued_key': payment.issued_key
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': result.get('error', 'Ошибка генерации ключа') if result else 'Ошибка генерации ключа'
                })

        else:
            from .night_vpn_fgn_service import process_night_vpn_payment_sync
            result = process_night_vpn_payment_sync(payment.payment_id)
            payment.refresh_from_db()
            if result and result.get('success'):
                return JsonResponse({
                    'success': True,
                    'message': 'Платеж подтвержден, ключ выдан',
                    'issued_key': payment.issued_key
                })
            else:
                error_msg = result.get('error', 'Ошибка генерации ключа') if result else 'Ошибка'
                logger.warning(f"Не удалось выдать ключ через bypass API: {error_msg}, пробуем пул ключей")
                from .services import PaymentService
                payment_service = PaymentService()
                success = payment_service._confirm_payment_with_key_pool(payment)
                payment.refresh_from_db()
                if success:
                    return JsonResponse({
                        'success': True,
                        'message': 'Платеж подтвержден, ключ выдан',
                        'issued_key': payment.issued_key
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'message': 'Не удалось выдать ключ'
                    })

    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Платеж не найден'}, status=404)
    except Exception as e:
        logger.error(f"Ошибка подтверждения платежа {payment_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def payment_actions(request, payment_id, action):
    """Обработка действий с платежами"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)
        
        if action == 'confirm':
            payment.status = 'succeeded'
            payment.save()
            message = 'Платеж подтвержден'
        elif action == 'cancel':
            payment.status = 'canceled'
            payment.save()
            message = 'Платеж отменен'
        else:
            return JsonResponse({'error': 'Неверное действие'}, status=400)
        
        return JsonResponse({'success': True, 'message': message})
            
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Платеж не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@login_required
def broadcast_create(request):
    """Создание рассылки"""
    if request.method == 'POST':
        try:
            message_text = request.POST.get('message_text')
            if not message_text:
                return JsonResponse({'error': 'Текст сообщения обязателен'}, status=400)
            
            # Получаем или создаем AdminUser
            from .models import AdminUser
            admin_user, created = AdminUser.objects.get_or_create(
                admin_id=request.user.id,
                defaults={'name': request.user.username or 'Admin'}
            )
            
            # Создаем рассылку
            broadcast = Broadcast.objects.create(
                admin=admin_user,
                message_text=message_text,
                status='pending'
            )
            
            # Запускаем рассылку в фоне
            from .services import BroadcastService
            broadcast_service = BroadcastService()
            broadcast_service.send_broadcast(broadcast)
            
            return JsonResponse({'success': True, 'broadcast_id': broadcast.broadcast_id})
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    # Получаем статистику пользователей для отображения
    from .models import TelegramUser, Payment
    
    total_users = TelegramUser.objects.count()
    active_users = TelegramUser.objects.filter(
        payments__status='succeeded'
    ).distinct().count()
    
    from django.utils import timezone
    from datetime import timedelta
    today = timezone.now().date()
    new_users_today = TelegramUser.objects.filter(
        created_at__date=today
    ).count()
    
    users_with_subscriptions = TelegramUser.objects.filter(
        payments__status='succeeded',
        payments__subscription_type__in=['month', 'year']
    ).distinct().count()
    
    context = {
        'total_users': total_users,
        'active_users': active_users,
        'new_users_today': new_users_today,
        'users_with_subscriptions': users_with_subscriptions
    }
    
    return render(request, 'bot_management/broadcast_create.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def send_support_message(request):
    """API для отправки сообщения поддержки"""
    try:
        # Пробуем получить данные из JSON или form data
        if request.content_type == 'application/json':
            data = json.loads(request.body)
            chat_id = data.get('chat_id')
            message_text = data.get('message_text')
        else:
            # Form data
            chat_id = request.POST.get('chat_id')
            message_text = request.POST.get('message')
        
        if not chat_id or not message_text:
            return JsonResponse({'error': 'Недостаточно данных'}, status=400)
        
        # Отправляем сообщение через сервис
        support_service = SupportService()
        success = support_service.send_message_to_user_sync(chat_id, message_text)
        
        if success:
            return JsonResponse({'status': 'success', 'message': 'Сообщение отправлено'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Ошибка отправки'})
            
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)})
    
    return JsonResponse({'status': 'error', 'message': 'Только POST запросы'})


@staff_member_required
def send_support_page(request):
    """Страница для отправки сообщений поддержки"""
    return render(request, 'bot_management/send_support.html')


@csrf_exempt
def simple_test(request):
    """Простая тестовая функция"""
    return JsonResponse({'status': 'success', 'message': 'Тест работает!'})


@csrf_exempt
def test_support_simple(request):
    """Простая функция для тестирования поддержки"""
    if request.method == 'POST':
        try:
            chat_id = request.POST.get('chat_id')
            message = request.POST.get('message')
            
            if not chat_id or not message:
                return JsonResponse({'status': 'error', 'message': 'Неверные параметры'})
            
            # Просто возвращаем успех без отправки
            return JsonResponse({'status': 'success', 'message': f'Получено: chat_id={chat_id}, message={message}'})
            
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    return JsonResponse({'status': 'error', 'message': 'Только POST запросы'})


@csrf_exempt
@require_http_methods(["POST"])
def set_user_entry_method(request):
    """API для установки способа первого входа пользователя"""
    try:
        user_id = int(request.POST.get('user_id', 0))
        entry_method = request.POST.get('entry_method', 'direct')
        
        if not user_id or entry_method not in ['direct', 'referral']:
            return JsonResponse({
                'success': False,
                'message': 'Неверные параметры'
            }, status=400)
        
        # Получаем или создаем пользователя
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={
                'first_entry_method': entry_method,
                'multi_level_referral_enabled': False
            }
        )
        
        # Если пользователь уже существует и у него еще не установлен способ входа
        if not created and not user.first_entry_method:
            user.first_entry_method = entry_method
            user.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Способ входа установлен: {entry_method}',
            'created': created
        })
        
    except Exception as e:
        logger.error(f"Ошибка установки способа входа: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка сервера'
        }, status=500)


# Дополнительные функции для совместимости с URL
@method_decorator(csrf_exempt, name='dispatch')
class BotWebhookView(View):
    """Универсальный Webhook для всех платежных систем (Platega, Antilopay и др.)"""
    def post(self, request):
        import json
        from config import PLATEGA_MERCHANT_ID, PLATEGA_SECRET
        from .platega_service import PlategaService
        
        logger.info("DEBUG: Получен POST запрос на универсальный webhook")
        
        # Проверка авторизации через заголовки (для Platega)
        merchant_id = request.headers.get('X-MerchantId')
        secret = request.headers.get('X-Secret')
        
        # Если заголовки присутствуют, проверяем их
        if merchant_id and secret:
            if merchant_id != PLATEGA_MERCHANT_ID or secret != PLATEGA_SECRET:
                logger.error(f"DEBUG: Неверные учетные данные")
                return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
            logger.info("DEBUG: Авторизация Platega успешна")
        else:
            logger.info("DEBUG: Запрос без авторизации Platega (универсальный webhook)")
        
        # Получаем данные callback из JSON тела
        try:
            callback_data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(f"DEBUG: Ошибка парсинга JSON: {e}")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        
        logger.info(f"DEBUG: Получен webhook: {callback_data}")
        
        # Валидация обязательных полей (поддерживаем как 'id', так и 'order_id')
        transaction_id = callback_data.get('id') or callback_data.get('order_id')
        status = callback_data.get('status')
        amount = callback_data.get('amount')
        currency = callback_data.get('currency')
        
        required_fields = ['status']
        if not transaction_id:
            required_fields.append('id или order_id')
        
        missing_fields = [field for field in required_fields if field not in callback_data and not (field == 'id или order_id' and transaction_id)]
        if missing_fields:
            logger.error(f"DEBUG: Отсутствуют обязательные поля: {missing_fields}")
            return JsonResponse({'status': 'error', 'message': f'Missing required fields: {missing_fields}'}, status=400)
        
        # Нормализуем данные для PlategaService
        normalized_data = {
            'id': transaction_id,
            'status': status,
            'amount': amount,
            'currency': currency,
            'paymentMethod': callback_data.get('paymentMethod'),
            'payload': callback_data.get('payload', ''),
            'order_id': callback_data.get('order_id'),
            'description': callback_data.get('description'),
            'created_at': callback_data.get('created_at'),
        }
        
        logger.info(f"DEBUG: Transaction ID: {transaction_id}, Status: {status}")
        
        # Обрабатываем callback через сервис
        result = PlategaService.process_webhook(normalized_data, merchant_id=merchant_id, secret=secret)
        
        if result:
            logger.info("DEBUG: Callback успешно обработан")
            return JsonResponse({'status': 'ok'}, status=200)
        else:
            logger.warning("DEBUG: Callback обработан с ошибкой")
            return JsonResponse({'status': 'ok', 'message': 'Received but processing failed'}, status=200)
    
    def get(self, request):
        """Health check для webhook"""
        return JsonResponse({'status': 'ok'})


@csrf_exempt
def yookassa_webhook(request):
    """Webhook для ЮKassa"""
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@require_http_methods(["POST"])
def platega_webhook(request):
    """
    Webhook для получения callback от Platega об изменении статуса транзакции
    Согласно документации: https://docs.platega.io/callback-об-изменении-статуса-транзакции-22645075e0
    
    Callback приходит с заголовками:
    - X-MerchantId: Ваш MerchantId (UUID)
    - X-Secret: Ваш API ключ
    
    JSON тело содержит:
    - id: UUID транзакции
    - amount: сумма
    - currency: валюта
    - status: CONFIRMED (успешная оплата) или CANCELED (неуспешная)
    - paymentMethod: ID метода оплаты
    - payload: дополнительные данные
    
    Важно: Нужно вернуть 200 OK в течение 60 секунд, иначе будет повторная попытка (до 3 раз с интервалом 5 минут)
    """
    try:
        import json
        from config import PLATEGA_MERCHANT_ID, PLATEGA_SECRET
        from .platega_service import PlategaService
        
        logger.info("DEBUG: Получен POST запрос на Platega callback")
        
        # Проверка авторизации через заголовки (согласно документации)
        merchant_id = request.headers.get('X-MerchantId')
        secret = request.headers.get('X-Secret')
        
        if not merchant_id or not secret:
            logger.error("DEBUG: Отсутствуют обязательные заголовки X-MerchantId или X-Secret")
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
        
        # Проверяем учетные данные
        if merchant_id != PLATEGA_MERCHANT_ID or secret != PLATEGA_SECRET:
            logger.error(f"DEBUG: Неверные учетные данные. Ожидалось: {PLATEGA_MERCHANT_ID}, получено: {merchant_id}")
            return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
        
        logger.info("DEBUG: Авторизация успешна")
        
        # Получаем данные callback из JSON тела
        try:
            callback_data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(f"DEBUG: Ошибка парсинга JSON: {e}")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        
        logger.info(f"DEBUG: Получен callback от Platega: {callback_data}")
        
        # Валидация обязательных полей согласно документации
        required_fields = ['id', 'amount', 'currency', 'status', 'paymentMethod']
        missing_fields = [field for field in required_fields if field not in callback_data]
        if missing_fields:
            logger.error(f"DEBUG: Отсутствуют обязательные поля: {missing_fields}")
            return JsonResponse({'status': 'error', 'message': f'Missing required fields: {missing_fields}'}, status=400)
        
        # Обрабатываем callback через сервис
        result = PlategaService.process_webhook(callback_data, merchant_id=merchant_id, secret=secret)
        
        if result:
            logger.info("DEBUG: Callback успешно обработан")
            # Platega ожидает ответ 200 OK в течение 60 секунд
            return JsonResponse({'status': 'ok'}, status=200)
        else:
            logger.warning("DEBUG: Callback обработан с ошибкой")
            # Все равно возвращаем 200, чтобы Platega не повторял запрос
            return JsonResponse({'status': 'ok', 'message': 'Received but processing failed'}, status=200)
            
    except Exception as e:
        logger.error(f"DEBUG: Ошибка обработки callback: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Platega ожидает ответ 200, даже при ошибке (чтобы не повторял запрос)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def create_antilopay_payment(request):
    """API для создания платежа через Antilopay (СБП)"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        subscription_type = data.get('subscription_type')
        vpn_type = data.get('vpn_type', 'night')
        is_renewal = data.get('is_renewal', False)
        renewal_for_payment_id = data.get('renewal_for_payment_id')
        delay = data.get('delay', 0)

        if not all([user_id, subscription_type]):
            return JsonResponse({'status': 'error', 'message': 'Недостаточно данных'}, status=400)

        if amount is None:
            amount = get_price(subscription_type)

        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={'username': f'user_{user_id}'}
        )

        renewal_for_payment = None
        if is_renewal and renewal_for_payment_id:
            try:
                renewal_for_payment = Payment.objects.get(payment_id=renewal_for_payment_id, user=user)
            except Payment.DoesNotExist:
                logger.warning(f"Оригинальный платеж {renewal_for_payment_id} не найден для пользователя {user_id}")

        payment = Payment.objects.create(
            user=user,
            amount=amount,
            subscription_type=subscription_type,
            status='pending',
            vpn_type=vpn_type,
            is_renewal=is_renewal,
            renewal_for_payment=renewal_for_payment,
        )

        from .antilopay_service import AntilopayService
        from config import ANTILOPAY_SECRET_ID, ANTILOPAY_PRIVATE_KEY, ANTILOPAY_PROJECT_ID

        if not ANTILOPAY_SECRET_ID or not ANTILOPAY_PRIVATE_KEY or not ANTILOPAY_PROJECT_ID:
            logger.error("Antilopay credentials не установлены")
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': 'Платежная система Antilopay не настроена. Обратитесь к администратору.'
            }, status=500)

        bot_username = "webnetvpn_robot"
        success_url = f"https://t.me/{bot_username}?start=payment_success_{payment.payment_id}"
        fail_url = f"https://t.me/{bot_username}?start=payment_failed_{payment.payment_id}"

        logger.info(f"Создание платежа Antilopay: payment_id={payment.payment_id}, amount={amount}")

        try:
            antilopay_data = AntilopayService.create_payment(payment, success_url=success_url, fail_url=fail_url, delay=delay)
        except Exception as e:
            logger.error(f"Исключение при создании платежа Antilopay: {e}")
            import traceback
            logger.error(traceback.format_exc())
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': f'Ошибка создания платежа Antilopay: {str(e)}'
            }, status=500)

        if antilopay_data:
            logger.info(f"Платеж Antilopay успешно создан: payment_id={antilopay_data.get('payment_id')}")
            return JsonResponse({
                'status': 'success',
                'payment_id': payment.payment_id,
                'transaction_id': antilopay_data.get('payment_id'),
                'confirmation_url': antilopay_data.get('payment_url'),
                'amount': float(payment.amount),
                'subscription_type': subscription_type
            })
        else:
            logger.error(f"Не удалось создать платеж Antilopay для payment_id={payment.payment_id}")
            payment.status = 'failed'
            payment.save()
            return JsonResponse({
                'status': 'error',
                'message': 'Antilopay API вернул ошибку. Подробности в логах сервера.'
            }, status=500)

    except Exception as e:
        logger.error(f"Ошибка создания платежа Antilopay: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def antilopay_webhook(request):
    """
    Webhook для получения callback от Antilopay об изменении статуса платежа.

    Antilopay отправляет POST запрос с JSON телом и заголовком X-Apay-Callback (подпись).
    IP адреса: 81.177.221.226, 87.228.9.243

    Статусы: SUCCESS, FAIL, CANCEL, EXPIRED
    """
    try:
        from .antilopay_service import AntilopayService

        logger.info("Получен POST запрос на Antilopay webhook")

        try:
            callback_data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON Antilopay: {e}")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        # Проверяем подпись callback
        signature = request.META.get('HTTP_X_APAY_CALLBACK', '')
        if signature:
            raw_body = request.body.decode('utf-8')
            if not AntilopayService.verify_callback_signature(raw_body, signature):
                logger.error("Неверная подпись callback Antilopay")
                return JsonResponse({'status': 'error', 'message': 'Invalid signature'}, status=403)
        else:
            logger.warning("Callback без подписи X-Apay-Callback")

        logger.info(f"Получен callback от Antilopay: {callback_data}")

        result = AntilopayService.process_webhook(callback_data, skip_notification=False)

        if result:
            logger.info("Antilopay callback успешно обработан")
            return JsonResponse({'status': 'ok'}, status=200)
        else:
            logger.warning("Antilopay callback обработан с ошибкой")
            return JsonResponse({'status': 'ok', 'message': 'Received but processing failed'}, status=200)

    except Exception as e:
        logger.error(f"Ошибка обработки Antilopay callback: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'status': 'ok', 'message': str(e)}, status=200)


@csrf_exempt
@require_http_methods(["GET"])
def check_antilopay_payment_status(request, payment_id):
    """API для проверки статуса платежа через Antilopay API по payment_id"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)

        if not payment.antilopay_payment_id:
            return JsonResponse({
                'success': False,
                'message': 'У платежа нет antilopay_payment_id'
            }, status=400)

        from .antilopay_service import AntilopayService
        from config import ANTILOPAY_PROJECT_ID

        antilopay_status = AntilopayService.check_payment(ANTILOPAY_PROJECT_ID, str(payment.payment_id))

        if not antilopay_status:
            return JsonResponse({
                'success': False,
                'message': 'Не удалось получить статус от Antilopay'
            }, status=500)

        ap_status = antilopay_status.get('status', 'unknown')
        ap_status_normalized = ap_status.upper() if ap_status else 'UNKNOWN'

        # Сохраняем recurrent_id из ответа check_payment если есть
        ap_recurrent_id = antilopay_status.get('recurrent_id') or antilopay_status.get('recurrent_id')
        if ap_recurrent_id and not payment.antilopay_recurrent_id:
            payment.antilopay_recurrent_id = ap_recurrent_id
            payment.save(update_fields=['antilopay_recurrent_id'])
            logger.info(f"Сохранён recurrent_id из check_payment для {payment.payment_id}: {ap_recurrent_id}")

        # Используем amount (фактически списанная сумма) для определения реальной оплаты
        # При binding amount=0, original_amount — сумма при создании
        ap_amount = antilopay_status.get('amount')
        ap_original_amount = antilopay_status.get('original_amount')

        # Реальная оплата: amount > 0
        if ap_status_normalized == 'SUCCESS' and payment.status != 'succeeded':
            if ap_amount is not None and float(ap_amount) > 0:
                logger.info(f"Платеж {payment.payment_id} успешен в Antilopay (amount={ap_amount}), подтверждаем")
                AntilopayService._handle_payment_success(payment, skip_notification=True)
                payment.refresh_from_db()
            elif payment.antilopay_recurrent_id:
                # amount=0 (binding) — проверяем рекуррент и его платежи
                try:
                    recurrent_status_data = AntilopayService.check_recurrent_payment_status(payment.antilopay_recurrent_id)
                    if recurrent_status_data:
                        r_status = recurrent_status_data.get('status', '')
                        logger.info(f"Проверка рекуррента {payment.antilopay_recurrent_id}: {r_status}")

                        # Ищем успешный charge среди платежей рекуррента
                        payments = recurrent_status_data.get('payments', [])
                        for pmt in payments:
                            pmt_amount = pmt.get('amount', 0)
                            pmt_status = pmt.get('status', '')
                            if float(pmt_amount) > 0 and pmt_status == 'SUCCESS':
                                logger.info(f"Найден успешный charge рекуррента: payment_id={pmt.get('payment_id')}, amount={pmt_amount}")
                                payment.amount = pmt_amount
                                payment.save(update_fields=['amount'])
                                AntilopayService._handle_payment_success(payment, skip_notification=True)
                                payment.refresh_from_db()
                                break
                        else:
                            # Нет успешных charge — смотрим статус рекуррента
                            if r_status in ('ACTIVE', 'WAIT_CONFIRM', 'PROCESSING', 'CREATED'):
                                pass  # ждём списание
                            elif r_status in ('CANCEL', 'PROVIDER_CANCEL', 'ERROR'):
                                logger.warning(f"Рекуррент {payment.antilopay_recurrent_id} в статусе {r_status}")
                                return JsonResponse({
                                    'success': True,
                                    'antilopay_status': ap_status,
                                    'payment_status': payment.status,
                                    'payment_id': payment.payment_id,
                                    'antilopay_payment_id': payment.antilopay_payment_id,
                                    'issued_key': None,
                                    'vpn_type': getattr(payment, 'vpn_type', 'night'),
                                    'subscription_type': payment.subscription_type,
                                    'is_binding': False,
                                    'recurrent_error': True,
                                    'recurrent_status': r_status,
                                    'message': f'Ошибка автоплатежа: рекуррент в статусе {r_status}. Обратитесь в поддержку.',
                                })
                except Exception as e:
                    logger.error(f"Ошибка проверки рекуррента: {e}")

        is_binding = (
            payment.status == 'pending'
            and ap_status_normalized == 'SUCCESS'
            and ap_amount is not None and float(ap_amount) == 0
        )

        # Проверяем, является ли это привязкой для пробного доступа
        is_trial_binding = False
        if is_binding:
            vpn_type = getattr(payment, 'vpn_type', 'night')
            user = payment.user
            trial_used_field = f'trial_key_used_{vpn_type}'
            if not getattr(user, trial_used_field, False):
                is_trial_binding = True

        if ap_status_normalized == 'SUCCESS' and payment.status != 'succeeded' and not is_binding:
            logger.info(f"Платеж {payment.payment_id} успешен в Antilopay, подтверждаем")

            AntilopayService._handle_payment_success(payment, skip_notification=True)
            payment.refresh_from_db()

        return JsonResponse({
            'success': True,
            'antilopay_status': ap_status,
            'payment_status': payment.status,
            'payment_id': payment.payment_id,
            'antilopay_payment_id': payment.antilopay_payment_id,
            'issued_key': payment.issued_key if payment.status == 'succeeded' else None,
            'vpn_type': getattr(payment, 'vpn_type', 'night'),
            'subscription_type': payment.subscription_type,
            'subscription_expires_at': payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M') if payment.subscription_expires_at else None,
            'is_binding': is_binding,
            'is_trial_binding': is_trial_binding,
        })

    except Payment.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'Платеж не найден'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка проверки статуса платежа Antilopay: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
def cryptobot_webhook(request):
    """
    Webhook для получения уведомлений от CryptoBot об оплате invoice.
    
    CryptoBot отправляет webhook при изменении статуса invoice.
    Нас интересует событие 'invoice_paid' - успешная оплата.
    """
    try:
        import json
        from .cryptobot_service import CryptobotService

        logger.info("Получен POST запрос на CryptoBot webhook")

        # Получаем данные webhook из JSON тела
        try:
            webhook_data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        logger.info(f"Получен webhook от CryptoBot: {webhook_data}")

        # Обрабатываем webhook через сервис
        result = CryptobotService.process_webhook(webhook_data)

        if result:
            logger.info("Webhook успешно обработан")
            return JsonResponse({'status': 'ok'}, status=200)
        else:
            logger.warning("Webhook обработан с ошибкой")
            return JsonResponse({'status': 'error', 'message': 'Processing failed'}, status=200)

    except Exception as e:
        logger.error(f"Ошибка обработки CryptoBot webhook: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'status': 'error', 'message': str(e)}, status=200)


@csrf_exempt
def test_webhook(request):
    """Тестовый webhook"""
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def get_yookassa_payment_status(request, payment_id):
    """Получение статуса платежа через ЮKassa"""
    return JsonResponse({'status': 'pending'})


@csrf_exempt
def test_support_send(request):
    """Тестовая отправка поддержки"""
    return JsonResponse({'status': 'ok'})


@login_required
def support_chat_list(request):
    """Список чатов поддержки"""
    from .models import SupportChat
    
    # Получаем все чаты поддержки
    support_chats = SupportChat.objects.select_related('user').prefetch_related('messages').all().order_by('-created_at')
    
    # Подсчитываем статистику
    total_chats = support_chats.count()
    open_chats = support_chats.filter(status='open').count()
    
    # Добавляем последнее сообщение для каждого чата
    for chat in support_chats:
        chat.last_message = chat.messages.order_by('-sent_at').first()
    
    context = {
        'support_chats': support_chats,
        'total_chats': total_chats,
        'open_chats': open_chats,
    }
    
    return render(request, 'bot_management/support_chat_list.html', context)


@login_required
def support_chat_detail(request, chat_id):
    """Детали чата поддержки"""
    try:
        from .models import SupportChat
        chat = SupportChat.objects.get(chat_id=chat_id)
        
        # Получаем сообщения чата
        messages = chat.messages.all().order_by('sent_at')
        
        context = {
            'chat': chat,
            'messages': messages,
        }
        
        return render(request, 'bot_management/support_chat_detail.html', context)
    except SupportChat.DoesNotExist:
        from django.http import Http404
        raise Http404("Чат не найден")


@csrf_exempt
def support_reply(request, chat_id):
    """Ответ в чат поддержки"""
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def create_support_chat(request):
    """Создание чата поддержки"""
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def toggle_support_chat(request, chat_id):
    """Переключение чата поддержки"""
    try:
        from .models import SupportChat
        
        # Находим чат
        chat = SupportChat.objects.get(chat_id=chat_id)
        
        # Переключаем статус
        if chat.status == 'open':
            chat.status = 'closed'
        else:
            chat.status = 'open'
        
        chat.save()
        
        return JsonResponse({'status': 'ok', 'new_status': chat.status})
        
    except SupportChat.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Чат не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def delete_support_chat(request, chat_id):
    """Удаление чата поддержки"""
    try:
        from .models import SupportChat
        
        # Находим и удаляем чат
        chat = SupportChat.objects.get(chat_id=chat_id)
        chat.delete()
        
        return JsonResponse({'status': 'ok', 'message': 'Чат удален'})
        
    except SupportChat.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Чат не найден'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def receive_support_message(request):
    """Получение сообщения поддержки"""
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def yookassa_webhook(request):
    """Webhook для получения уведомлений от ЮKassa"""
    try:
        import json
        from .models import Payment, BalanceTransaction
        
        logger.info("DEBUG: Получен запрос на webhook")
        
        # Получаем данные webhook
        webhook_data = json.loads(request.body)
        logger.info(f"DEBUG: Получен webhook от ЮKassa: {webhook_data}")
        
        # Обрабатываем webhook
        event = webhook_data.get('event')
        payment_object = webhook_data.get('object', {})
        yookassa_payment_id = payment_object.get('id')
        metadata = payment_object.get('metadata', {})
        our_payment_id = metadata.get('payment_id')
        
        logger.info(f"DEBUG: Событие: {event}, YooKassa ID: {yookassa_payment_id}, Наш ID: {our_payment_id}")
        
        if event == 'payment.succeeded' or event == 'payment.waiting_for_capture':
            logger.info(f"DEBUG: Платеж {event} - обрабатываем")
            
            if our_payment_id:
                try:
                    # Конвертируем payment_id в int
                    our_payment_id = int(our_payment_id)
                    logger.info(f"DEBUG: Ищем платеж с ID: {our_payment_id}")
                    
                    # Проверяем, существует ли платеж
                    try:
                        payment = Payment.objects.get(payment_id=our_payment_id)
                        logger.info(f"DEBUG: Найден платеж в БД: {payment.payment_id}, тип: {payment.subscription_type}")
                    except Payment.DoesNotExist:
                        logger.error(f"DEBUG: Платеж {our_payment_id} не найден в БД")
                        # Выводим все платежи для отладки
                        all_payments = Payment.objects.all().order_by('-payment_id')[:5]
                        logger.info(f"DEBUG: Последние 5 платежей: {[p.payment_id for p in all_payments]}")
                        return JsonResponse({'status': 'error', 'message': 'Платеж не найден'}, status=404)
                    
                    # Если это платеж пополнения баланса, обрабатываем его
                    if payment.subscription_type == 'balance_deposit':
                        try:
                            balance_transaction = BalanceTransaction.objects.get(payment=payment)
                            
                            # Проверяем настройку автоматического подтверждения
                            from .models import BotSettings
                            auto_capture = BotSettings.get_setting('auto_capture_payments', 'true').lower() == 'true'
                            
                            if auto_capture:
                                # Сначала подтверждаем платеж в YooKassa
                                if payment.yookassa_payment_id:
                                    from .yookassa_service import YooKassaService
                                    from config import YOOKASSA_SECRET_KEY
                                    is_test_mode = YOOKASSA_SECRET_KEY.startswith('test_')
                                    
                                    if is_test_mode:
                                        # В тестовом режиме симулируем успешное подтверждение
                                        capture_success = True
                                        logger.info(f"DEBUG: Тестовый режим - симулируем подтверждение платежа {payment.yookassa_payment_id}")
                                    else:
                                        capture_success = YooKassaService.capture_payment(payment.yookassa_payment_id, float(balance_transaction.amount))
                                    
                                    if capture_success:
                                        logger.info(f"DEBUG: Платеж {payment.yookassa_payment_id} автоматически подтвержден в YooKassa")
                                    else:
                                        logger.warning(f"DEBUG: Не удалось автоматически подтвердить платеж {payment.yookassa_payment_id} в YooKassa")
                            else:
                                logger.info(f"DEBUG: Автоматическое подтверждение отключено, платеж {payment.payment_id} ожидает ручного подтверждения")
                                # Если автоматическое подтверждение отключено, не обновляем статус
                                return JsonResponse({'status': 'ok', 'message': 'Платеж ожидает ручного подтверждения'})
                            
                            # Обновляем статус платежа только если автоматическое подтверждение включено
                            payment.status = 'succeeded'
                            payment.paid_at = timezone.now()
                            payment.save()
                            
                            # Обновляем баланс пользователя
                            payment.user.balance += balance_transaction.amount
                            payment.user.save()
                            
                            # Обновляем статус транзакции
                            balance_transaction.status = 'completed'
                            balance_transaction.completed_at = timezone.now()
                            balance_transaction.save()
                            
                            logger.info(f"DEBUG: Баланс пользователя {payment.user.user_id} пополнен на {balance_transaction.amount} ₽")
                            logger.info(f"DEBUG: Новый баланс: {payment.user.balance} ₽")
                            
                            # Отправляем уведомление пользователю
                            try:
                                from aiogram import Bot
                                from config import BOT_TOKEN
                                import asyncio
                                
                                notification_message = f'✅ <b>Баланс пополнен!</b>\n\n💰 <b>Сумма:</b> {balance_transaction.amount} ₽\n💳 <b>Новый баланс:</b> {payment.user.balance} ₽\n\n<i>Спасибо за пополнение! 🚀</i>'
                                
                                # Запускаем уведомление в новом event loop
                                try:
                                    loop = asyncio.get_event_loop()
                                except RuntimeError:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                
                                bot = Bot(token=BOT_TOKEN)
                                loop.run_until_complete(bot.send_message(chat_id=payment.user.user_id, text=notification_message, parse_mode='HTML'))
                                loop.run_until_complete(bot.session.close())
                                
                                logger.info(f"DEBUG: Уведомление отправлено пользователю {payment.user.user_id}")
                                
                            except Exception as notify_e:
                                logger.error(f"DEBUG: Ошибка отправки уведомления: {notify_e}")
                            
                        except BalanceTransaction.DoesNotExist:
                            logger.error(f"DEBUG: Транзакция баланса не найдена для платежа {our_payment_id}")
                    else:
                        logger.info(f"DEBUG: Обрабатываем обычный платеж подписки: {payment.subscription_type}")
                        # Здесь можно добавить логику для обычных платежей подписки
                    
                    return JsonResponse({'status': 'ok', 'message': 'Платеж успешно обработан'})
                    
                except Payment.DoesNotExist:
                    logger.error(f"DEBUG: Платеж {our_payment_id} не найден в БД")
                    return JsonResponse({'status': 'error', 'message': 'Платеж не найден'}, status=404)
            else:
                logger.warning("DEBUG: Не найден наш ID платежа в метаданных")
                return JsonResponse({'status': 'ok', 'message': 'Платеж обработан (без нашего ID)'})
                
        elif event == 'payment.canceled':
            logger.info("DEBUG: Платеж отменен")
            if our_payment_id:
                try:
                    payment = Payment.objects.get(payment_id=our_payment_id)
                    payment.status = 'canceled'
                    payment.save()
                    logger.info(f"DEBUG: Платеж {our_payment_id} отменен")
                except Payment.DoesNotExist:
                    logger.error(f"DEBUG: Платеж {our_payment_id} не найден для отмены")
            return JsonResponse({'status': 'ok', 'message': 'Платеж отменен'})
        else:
            logger.warning(f"DEBUG: Неизвестное событие: {event}")
            return JsonResponse({'status': 'ok', 'message': 'Событие получено'})
            
    except Exception as e:
        logger.error(f"DEBUG: Ошибка webhook: {e}")
        import traceback
        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def toggle_auto_capture(request):
    """Включить/выключить автоматическое подтверждение платежей"""
    try:
        from .models import BotSettings
        
        data = json.loads(request.body)
        enabled = data.get('enabled', True)
        
        BotSettings.set_setting(
            'auto_capture_payments', 
            'true' if enabled else 'false',
            'Автоматическое подтверждение платежей через webhook'
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Автоматическое подтверждение {"включено" if enabled else "отключено"}',
            'auto_capture': enabled
        })
        
    except Exception as e:
        logger.error(f"Ошибка переключения автоматического подтверждения: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_auto_capture_status(request):
    """Получить статус автоматического подтверждения"""
    try:
        from .models import BotSettings
        
        auto_capture = BotSettings.get_setting('auto_capture_payments', 'true').lower() == 'true'
        
        return JsonResponse({
            'success': True,
            'auto_capture': auto_capture
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса автоматического подтверждения: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def manual_capture_payment(request, payment_id):
    """Ручное подтверждение платежа"""
    try:
        from .models import Payment, BalanceTransaction, BotSettings
        from .yookassa_service import YooKassaService
        from django.db import transaction
        
        try:
            payment = Payment.objects.get(payment_id=payment_id, subscription_type='balance_deposit')
            balance_transaction = BalanceTransaction.objects.get(payment=payment)
            
            if payment.status == 'succeeded':
                return JsonResponse({'success': False, 'message': 'Платеж уже подтвержден'})
            
            # Подтверждаем платеж в YooKassa (только если это не тестовый режим)
            from config import YOOKASSA_SECRET_KEY
            is_test_mode = YOOKASSA_SECRET_KEY.startswith('test_')
            
            logger.info(f"DEBUG: Условия для подтверждения - is_test_mode: {is_test_mode}, yookassa_id: {payment.yookassa_payment_id}")
            
            # В тестовом режиме всегда пропускаем подтверждение в YooKassa
            if is_test_mode:
                logger.info(f"DEBUG: Тестовый режим - пропускаем подтверждение в YooKassa для платежа {payment_id}")
                # В тестовом режиме просто симулируем успешное подтверждение
                capture_success = True
            else:
                # Только в продакшн режиме подтверждаем в YooKassa
                if payment.yookassa_payment_id:
                    logger.info(f"DEBUG: Подтверждаем платеж в YooKassa")
                    capture_success = YooKassaService.capture_payment(payment.yookassa_payment_id, float(balance_transaction.amount))
                    
                    if not capture_success:
                        return JsonResponse({'success': False, 'message': 'Не удалось подтвердить платеж в YooKassa'})
                else:
                    logger.info(f"DEBUG: Нет YooKassa ID - пропускаем подтверждение в YooKassa для платежа {payment_id}")
                    capture_success = True
            
            with transaction.atomic():
                # Обновляем статус платежа
                paid_at = timezone.now()
                payment.status = 'succeeded'
                payment.paid_at = paid_at
                
                # Устанавливаем дату окончания подписки
                from datetime import timedelta
                subscription_expires_at = None
                if payment.subscription_type == 'week':
                    subscription_expires_at = paid_at + timedelta(days=7)
                elif payment.subscription_type == 'month':
                    subscription_expires_at = paid_at + timedelta(days=30)
                elif payment.subscription_type == '3months':
                    subscription_expires_at = paid_at + timedelta(days=90)
                elif payment.subscription_type == 'year':
                    subscription_expires_at = paid_at + timedelta(days=365)
                
                payment.subscription_expires_at = subscription_expires_at
                payment.save()

                # Запускаем отложенные напоминания об истечении подписки
                if subscription_expires_at:
                    from bot_with_django import schedule_subscription_expiry_reminders
                    import asyncio
                    asyncio.create_task(schedule_subscription_expiry_reminders(
                        payment.payment_id,
                        payment.user.user_id,
                        payment.subscription_type,
                        subscription_expires_at
                    ))
                
                # Обновляем баланс пользователя
                payment.user.balance += balance_transaction.amount
                payment.user.save()
                
                # Обновляем статус транзакции
                balance_transaction.status = 'completed'
                balance_transaction.completed_at = timezone.now()
                balance_transaction.save()
                
                logger.info(f"DEBUG: Платеж {payment_id} подтвержден вручную")
                return JsonResponse({
                    'success': True, 
                    'message': 'Платеж успешно подтвержден',
                    'amount': float(balance_transaction.amount),
                    'new_balance': float(payment.user.balance)
                })
                
        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Платеж не найден'}, status=404)
        except BalanceTransaction.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Транзакция не найдена'}, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка ручного подтверждения платежа: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def test_webhook(request):
    """Тестовый webhook"""
    return JsonResponse({'status': 'ok', 'message': 'Webhook работает'})


@csrf_exempt
@require_http_methods(["POST"])
def get_yookassa_payment_status(request, payment_id):
    """Получение статуса платежа через ЮKassa API"""
    try:
        from .yookassa_service import YooKassaService
        
        # Получаем статус платежа
        status_data = YooKassaService.get_payment_status(payment_id)
        
        if status_data:
            return JsonResponse({
                'success': True,
                'status': status_data.get('status'),
                'amount': status_data.get('amount'),
                'created_at': status_data.get('created_at')
            })
        else:
            return JsonResponse({'success': False, 'message': 'Платеж не найден'}, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка получения статуса платежа: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def withdrawal_notification_api(request):
    """API для получения уведомлений о заявках на вывод от бота"""
    try:
        data = json.loads(request.body)
        notification_type = data.get('notification_type')
        withdrawal_data = data.get('withdrawal_data')
        
        if not notification_type or not withdrawal_data:
            return JsonResponse({
                'success': False,
                'message': 'Не все обязательные поля заполнены'
            }, status=400)
        
        # Отправляем уведомление в Telegram через HTTP запрос к боту
        try:
            import requests
            
            # Отправляем запрос к боту через его HTTP API
            bot_notification_url = 'http://127.0.0.1:8023/api/withdrawal/notification/'  # Порт бота
            
            response = requests.post(bot_notification_url, json={
                'notification_type': notification_type,
                'withdrawal_data': withdrawal_data
            }, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"Уведомление {notification_type} отправлено в Telegram для заявки {withdrawal_data.get('withdrawal_id')}")
            else:
                logger.error(f"Ошибка отправки уведомления боту: {response.status_code} - {response.text}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления в Telegram: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Ошибка отправки уведомления: {str(e)}'
            }, status=500)
        
        return JsonResponse({'success': True, 'message': 'Уведомление отправлено'})
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка обработки уведомления о заявке на вывод: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Внутренняя ошибка сервера'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def upload_keys_api(request):
    """API для загрузки ключей списком"""
    try:
        data = json.loads(request.body)
        keys_text = data.get('keys')
        subscription_type = data.get('subscription_type')
        total_activations = data.get('total_activations', 1)
        
        if not keys_text or not subscription_type:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно данных: нужны keys и subscription_type'
            }, status=400)
        
        # Валидация subscription_type: только месячные и пробные (общая база для 3м/год)
        valid_types = ['trial', 'week', 'month']
        if subscription_type not in valid_types:
            return JsonResponse({
                'success': False,
                'message': f'Неверный тип подписки. Допустимые: {", ".join(valid_types)} (для 3 мес/год добавляйте месячные ключи)'
            }, status=400)

        # Валидация total_activations
        if total_activations not in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
            return JsonResponse({
                'success': False,
                'message': 'Количество активаций должно быть от 1 до 10'
            }, status=400)
        
        # Парсим ключи (разделитель - новая строка или запятая)
        keys_list = []
        for line in keys_text.split('\n'):
            line = line.strip()
            if line:
                # Если строка содержит запятые, разделяем по запятым
                if ',' in line:
                    keys_list.extend([k.strip() for k in line.split(',') if k.strip()])
                else:
                    keys_list.append(line)
        
        if not keys_list:
            return JsonResponse({
                'success': False,
                'message': 'Не найдено ни одного ключа'
            }, status=400)
        
        # Создаем ключи
        created_count = 0
        skipped_count = 0
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
                    created_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                errors.append(f"Ошибка при создании ключа {key_value}: {str(e)}")
        
        return JsonResponse({
            'success': True,
            'message': f'Загружено {created_count} ключей, пропущено {skipped_count}',
            'created': created_count,
            'skipped': skipped_count,
            'errors': errors if errors else None
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка загрузки ключей: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def update_price_api(request):
    """API для изменения цены подписки"""
    try:
        data = json.loads(request.body)
        subscription_type = data.get('subscription_type')
        price = data.get('price')
        
        if not subscription_type or price is None:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно данных: нужны subscription_type и price'
            }, status=400)
        
        # Валидация subscription_type
        valid_types = ['trial', 'week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            return JsonResponse({
                'success': False,
                'message': f'Неверный тип подписки. Допустимые: {", ".join(valid_types)}'
            }, status=400)

        # Валидация цены
        try:
            price = int(price)
            if price <= 0:
                raise ValueError("Цена должна быть положительным числом")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'message': 'Цена должна быть положительным целым числом'
            }, status=400)
        
        # Сохраняем цену в BotSettings
        from .models import BotSettings
        BotSettings.set_setting(
            f'price_{subscription_type}',
            str(price),
            f'Цена для подписки {subscription_type}'
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Цена для {subscription_type} обновлена на {price} ₽',
            'subscription_type': subscription_type,
            'price': price
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка обновления цены: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def update_subscription_name_api(request):
    """API для изменения названия подписки"""
    try:
        data = json.loads(request.body)
        subscription_type = data.get('subscription_type')
        name = data.get('name')
        
        if not subscription_type or not name:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно данных: нужны subscription_type и name'
            }, status=400)
        
        # Валидация subscription_type
        valid_types = ['trial', 'week', 'month', '3months', '6months', 'year']
        if subscription_type not in valid_types:
            return JsonResponse({
                'success': False,
                'message': f'Неверный тип подписки. Допустимые: {", ".join(valid_types)}'
            }, status=400)

        # Сохраняем название в BotSettings
        from .models import BotSettings
        BotSettings.set_setting(
            f'subscription_name_{subscription_type}',
            name,
            f'Название для подписки {subscription_type}'
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Название для {subscription_type} обновлено на "{name}"',
            'subscription_type': subscription_type,
            'name': name
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка обновления названия: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_prices_api(request):
    """API для получения текущих цен"""
    try:
        prices = {}
        for sub_type in ['week', 'month', '3months', '6months', 'year']:
            prices[sub_type] = get_price(sub_type)

        return JsonResponse({
            'success': True,
            'prices': prices
        })

    except Exception as e:
        logger.error(f"Ошибка получения цен: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def scheduler_status_api(request):
    """
    API endpoint для получения статуса планировщика уведомлений
    """
    try:
        logger.info("Получен запрос статуса планировщика")

        # Получаем статус планировщика
        status_data = get_scheduler_status()

        # Добавляем дополнительную информацию
        status_data['timestamp'] = timezone.now().isoformat()
        status_data['server_time'] = timezone.now().strftime('%d.%m.%Y %H:%M:%S')

        logger.info(f"Статус планировщика: {status_data.get('status', 'unknown')}")

        return JsonResponse({
            'success': True,
            'data': status_data
        })

    except Exception as e:
        logger.error(f"Ошибка получения статуса планировщика: {e}")
        return JsonResponse({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}',
            'data': {'status': 'error', 'error': str(e)}
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def validate_promo_code(request):
    """API для проверки промокода"""
    try:
        data = json.loads(request.body)
        code = data.get('code', '').strip().upper()
        user_id = data.get('user_id')

        if not code or not user_id:
            return JsonResponse({'success': False, 'message': 'Недостаточно данных'}, status=400)

        promo = PromoCode.objects.filter(code=code, is_active=True).first()
        if not promo:
            return JsonResponse({'success': False, 'message': 'Промокод не найден'})

        if promo.expires_at and promo.expires_at < timezone.now():
            return JsonResponse({'success': False, 'message': 'Срок действия промокода истёк'})

        if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
            return JsonResponse({'success': False, 'message': 'Промокод больше не действует (достигнут лимит)'})

        # Проверяем лимит использований на пользователя
        user_usage_count = PromoCodeUsage.objects.filter(promo_code=promo, user_id=user_id).count()
        max_per_user = getattr(promo, 'max_uses_per_user', 1)
        if max_per_user > 0 and user_usage_count >= max_per_user:
            return JsonResponse({'success': False, 'message': f'Вы уже использовали этот промокод (макс. {max_per_user} раз)'})

        return JsonResponse({
            'success': True,
            'discount_percent': promo.discount_percent,
            'promo_code_id': promo.id,
        })

    except Exception as e:
        logger.error(f"Ошибка проверки промокода: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)