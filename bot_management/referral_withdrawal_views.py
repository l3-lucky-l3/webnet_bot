from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
import json
import logging

from .models import TelegramUser, ReferralWithdrawal, ReferralBalanceTransaction, Payment
from .referral_services import ReferralService

logger = logging.getLogger(__name__)

def send_withdrawal_notification_to_bot(notification_type: str, withdrawal: ReferralWithdrawal):
    """Отправляет уведомление боту о изменении статуса заявки на вывод"""
    try:
        import requests
        import threading
        
        # Данные для уведомления
        withdrawal_data = {
            'user_id': withdrawal.user.user_id,
            'amount': float(withdrawal.amount),
            'payment_method': withdrawal.payment_method,
            'payment_details': withdrawal.payment_details,
            'username': withdrawal.user.username,
            'first_name': withdrawal.user.first_name,
            'withdrawal_id': withdrawal.id
        }
        
        def send_notification():
            try:
                # Отправляем POST запрос боту через HTTP
                bot_url = 'http://127.0.0.1:8123/bot_management/api/withdrawal/notification/'
                
                response = requests.post(bot_url, json={
                    'notification_type': notification_type,
                    'withdrawal_data': withdrawal_data
                }, timeout=10)
                
                if response.status_code == 200:
                    logger.info(f"Уведомление {notification_type} отправлено для заявки {withdrawal.id}")
                else:
                    logger.error(f"Ошибка отправки уведомления: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"Ошибка в потоке отправки уведомления: {e}")
        
        # Запускаем отправку в отдельном потоке
        thread = threading.Thread(target=send_notification)
        thread.daemon = True
        thread.start()
        
        logger.info(f"Запущен поток отправки уведомления {notification_type} для заявки {withdrawal.id}")
            
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления боту: {e}")


@csrf_exempt
@require_http_methods(["POST"])
def request_withdrawal(request):
    """API для запроса вывода реферальных средств"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        payment_method = data.get('payment_method')
        payment_details = data.get('payment_details')
        
        if not all([user_id, not amount is None, payment_method, payment_details]):
            return JsonResponse({
                'success': False,
                'message': 'Не все обязательные поля заполнены'
            }, status=400)
        
        # Проверяем, что пользователь существует
        try:
            user = TelegramUser.objects.get(user_id=user_id)
        except TelegramUser.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Пользователь не найден'
            }, status=404)
        
        # Проверяем минимальную сумму (500 рублей)
        min_withdrawal = 500
        if amount < min_withdrawal:
            return JsonResponse({
                'success': False,
                'message': f'Минимальная сумма для вывода: {min_withdrawal} ₽'
            }, status=400)
        
        # Проверяем, что на реферальном балансе достаточно средств
        if user.referral_balance < amount:
            return JsonResponse({
                'success': False,
                'message': 'Недостаточно средств на реферальном балансе'
            }, status=400)
        
        # Проверяем, что нет активных запросов на вывод
        active_withdrawals = ReferralWithdrawal.objects.filter(
            user=user,
            status__in=['pending', 'approved']
        )
        if active_withdrawals.exists():
            return JsonResponse({
                'success': False,
                'message': 'У вас уже есть активный запрос на вывод средств'
            }, status=400)
        
        # Создаем запрос на вывод
        with transaction.atomic():
            withdrawal = ReferralWithdrawal.objects.create(
                user=user,
                amount=amount,
                payment_method=payment_method,
                payment_details=payment_details
            )
            
            # Создаем транзакцию "запрос на вывод"
            ReferralBalanceTransaction.objects.create(
                user=user,
                transaction_type='withdrawal_request',
                amount=-amount,  # Отрицательная сумма для списания
                description=f'Запрос на вывод {amount} ₽ ({withdrawal.get_payment_method_display()})',
                withdrawal_request=withdrawal
            )
            
            # Пока не списываем с баланса - только при одобрении админом
            # user.referral_balance -= amount
            # user.save()
        
        logger.info(f"Создан запрос на вывод {amount} ₽ для пользователя {user_id}")
        
        return JsonResponse({
            'success': True,
            'message': 'Запрос на вывод средств создан и отправлен на рассмотрение',
            'withdrawal_id': withdrawal.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка создания запроса на вывод: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Внутренняя ошибка сервера'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_withdrawal_status(request, user_id):
    """Получение статуса запросов на вывод для пользователя"""
    try:
        user = get_object_or_404(TelegramUser, user_id=user_id)
        
        withdrawals = ReferralWithdrawal.objects.filter(user=user).order_by('-created_at')
        
        withdrawals_data = []
        for withdrawal in withdrawals:
            withdrawals_data.append({
                'id': withdrawal.id,
                'amount': float(withdrawal.amount),
                'payment_method': withdrawal.get_payment_method_display(),
                'status': withdrawal.get_status_display(),
                'status_code': withdrawal.status,
                'created_at': withdrawal.created_at.isoformat(),
                'processed_at': withdrawal.processed_at.isoformat() if withdrawal.processed_at else None,
                'admin_comment': withdrawal.admin_comment
            })
        
        return JsonResponse({
            'success': True,
            'withdrawals': withdrawals_data,
            'referral_balance': float(user.referral_balance)
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса выводов: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка получения данных'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_referral_balance(request, user_id):
    """Получение реферального баланса пользователя"""
    try:
        user = get_object_or_404(TelegramUser, user_id=user_id)
        
        # Получаем последние транзакции реферального баланса
        transactions = ReferralBalanceTransaction.objects.filter(
            user=user
        ).order_by('-created_at')[:10]
        
        transactions_data = []
        for transaction in transactions:
            transactions_data.append({
                'id': transaction.id,
                'type': transaction.get_transaction_type_display(),
                'amount': float(transaction.amount),
                'description': transaction.description,
                'created_at': transaction.created_at.isoformat()
            })
        
        return JsonResponse({
            'success': True,
            'referral_balance': float(user.referral_balance),
            'transactions': transactions_data
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения реферального баланса: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Ошибка получения данных'
        }, status=500)


@login_required
def withdrawal_management(request):
    """Админ панель для управления запросами на вывод"""
    withdrawals = ReferralWithdrawal.objects.select_related('user').order_by('-created_at')
    
    # Фильтрация по статусу
    status_filter = request.GET.get('status')
    if status_filter:
        withdrawals = withdrawals.filter(status=status_filter)
    
    context = {
        'withdrawals': withdrawals,
        'status_choices': ReferralWithdrawal.STATUS_CHOICES,
        'current_status': status_filter
    }
    
    return render(request, 'bot_management/withdrawal_management.html', context)


@login_required
def process_withdrawal(request, withdrawal_id):
    """Обработка запроса на вывод админом"""
    withdrawal = get_object_or_404(ReferralWithdrawal, id=withdrawal_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        admin_comment = request.POST.get('admin_comment', '')
        
        with transaction.atomic():
            if action == 'approve':
                # Одобряем запрос
                withdrawal.status = 'approved'
                withdrawal.admin_comment = admin_comment
                withdrawal.processed_at = timezone.now()
                withdrawal.processed_by = request.user
                withdrawal.save()
                
                # Создаем транзакцию "одобрение вывода"
                ReferralBalanceTransaction.objects.create(
                    user=withdrawal.user,
                    transaction_type='withdrawal_completed',
                    amount=0,  # Нулевая сумма для одобрения
                    description=f'Запрос на вывод одобрен администратором. Комментарий: {admin_comment}',
                    withdrawal_request=withdrawal
                )
                
                messages.success(request, f'Запрос на вывод {withdrawal.amount} ₽ одобрен')
                
                # Отправляем уведомление об одобрении
                send_withdrawal_notification_to_bot('approved', withdrawal)
                
            elif action == 'complete':
                # Завершаем выплату
                withdrawal.status = 'completed'
                withdrawal.admin_comment = admin_comment
                withdrawal.processed_at = timezone.now()
                withdrawal.processed_by = request.user
                withdrawal.save()
                
                # Списываем с реферального баланса
                withdrawal.user.referral_balance -= withdrawal.amount
                withdrawal.user.save()
                
                # Создаем транзакцию "выплата завершена"
                ReferralBalanceTransaction.objects.create(
                    user=withdrawal.user,
                    transaction_type='withdrawal_completed',
                    amount=-withdrawal.amount,
                    description=f'Выплата {withdrawal.amount} ₽ завершена. Комментарий: {admin_comment}',
                    withdrawal_request=withdrawal
                )
                
                messages.success(request, f'Выплата {withdrawal.amount} ₽ завершена')
                
                # Отправляем уведомление о завершении выплаты
                send_withdrawal_notification_to_bot('completed', withdrawal)
                
            elif action == 'reject':
                # Отклоняем запрос
                withdrawal.status = 'rejected'
                withdrawal.admin_comment = admin_comment
                withdrawal.processed_at = timezone.now()
                withdrawal.processed_by = request.user
                withdrawal.save()
                
                # Создаем транзакцию "отклонение"
                ReferralBalanceTransaction.objects.create(
                    user=withdrawal.user,
                    transaction_type='withdrawal_cancelled',
                    amount=0,
                    description=f'Запрос на вывод отклонен. Причина: {admin_comment}',
                    withdrawal_request=withdrawal
                )
                
                messages.warning(request, f'Запрос на вывод {withdrawal.amount} ₽ отклонен')
                
                # Отправляем уведомление об отклонении
                send_withdrawal_notification_to_bot('rejected', withdrawal)
        
        return redirect('bot_management:withdrawal_management')
    
    context = {
        'withdrawal': withdrawal
    }
    
    return render(request, 'bot_management/process_withdrawal.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def pay_with_referral_balance(request):
    """Оплата подписки реферальными средствами"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        subscription_type = data.get('subscription_type')
        vpn_type = data.get('vpn_type', 'night')
        
        # Приводим amount к Decimal (referral_balance — DecimalField)
        from decimal import Decimal
        try:
            amount = Decimal(str(amount))
        except (TypeError, ValueError, Exception):
            return JsonResponse({
                'success': False,
                'message': 'Неверная сумма платежа'
            }, status=400)
        
        if not user_id or not subscription_type:
            return JsonResponse({
                'success': False,
                'message': 'Не все обязательные поля заполнены'
            }, status=400)
        
        # Проверяем пользователя
        try:
            user = TelegramUser.objects.get(user_id=user_id)
        except TelegramUser.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Пользователь не найден'
            }, status=404)
        
        # Проверяем баланс
        if user.referral_balance < amount:
            return JsonResponse({
                'success': False,
                'message': f'Недостаточно средств. Баланс: {user.referral_balance} ₽, Требуется: {amount} ₽'
            }, status=400)
        
        # Списываем с реферального баланса и создаем платеж
        with transaction.atomic():
            user.referral_balance -= amount
            user.save()
            
            payment = Payment.objects.create(
                user=user,
                amount=amount,
                subscription_type=subscription_type,
                status='pending',
                vpn_type=vpn_type,
            )
            
            ReferralBalanceTransaction.objects.create(
                user=user,
                transaction_type='subscription_purchase',
                amount=-amount,
                description=f'Оплата подписки {subscription_type} ({vpn_type} VPN)'
            )
        
        # Выдаем ключ (вне транзакции, чтобы не блокировать)
        try:
            if vpn_type == 'night':
                from .night_vpn_fgn_service import process_night_vpn_payment_sync
                
                result = process_night_vpn_payment_sync(payment.payment_id)
                
                if result and result.get('success'):
                    issued_key = result.get('key_value')
                    payment.refresh_from_db()
                    
                    # Выдаём также ключ обычного VPN
                    try:
                        from .services import PaymentService
                        from config import BOT_TOKEN
                        from aiogram import Bot
                        
                        bot = Bot(token=BOT_TOKEN)
                        payment_service = PaymentService(bot=bot)
                        payment_service._issue_regular_vpn_key_for_night_payment(payment, night_key=issued_key)
                        payment.refresh_from_db()
                    except Exception as e:
                        logger.warning(f"Ошибка выдачи Regular VPN ключа: {e}")
                    
                    return JsonResponse({
                        'success': True,
                        'payment_id': payment.payment_id,
                        'issued_key': payment.issued_key,
                        'message': 'Подписка успешно оплачена реферальными средствами'
                    })
                else:
                    # Fallback на пул ключей
                    from .services import PaymentService
                    from config import BOT_TOKEN
                    from aiogram import Bot
                    
                    bot = Bot(token=BOT_TOKEN)
                    payment_service = PaymentService(bot=bot)
                    confirmed = payment_service._confirm_payment_with_key_pool(payment)
                    
                    if confirmed:
                        payment.refresh_from_db()
                        return JsonResponse({
                            'success': True,
                            'payment_id': payment.payment_id,
                            'issued_key': payment.issued_key,
                            'message': 'Подписка успешно оплачена реферальными средствами'
                        })
                    else:
                        return JsonResponse({
                            'success': False,
                            'message': 'Ошибка выдачи ключа. Обратитесь в поддержку'
                        }, status=500)
            else:
                # Regular VPN
                from .regular_vpn_service import process_regular_vpn_payment_success_sync
                
                result = process_regular_vpn_payment_success_sync(payment.payment_id)
                
                if result and result.get('success'):
                    payment.refresh_from_db()
                    return JsonResponse({
                        'success': True,
                        'payment_id': payment.payment_id,
                        'issued_key': payment.issued_key,
                        'message': 'Подписка успешно оплачена реферальными средствами'
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'message': 'Ошибка выдачи ключа. Обратитесь в поддержку'
                    }, status=500)
                
        except Exception as e:
            logger.error(f"Ошибка выдачи ключа: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return JsonResponse({
                'success': False,
                'message': f'Ошибка выдачи ключа: {str(e)}'
            }, status=500)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка оплаты реферальными средствами: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': 'Внутренняя ошибка сервера'
        }, status=500)
