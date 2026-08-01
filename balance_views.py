from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db import transaction
import json
import logging
# DDoS защита убрана - только для бота

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["GET"])
def get_user_profile(request, user_id):
    """Получить профиль пользователя"""
    try:
        from .models import TelegramUser
        from django.db import transaction
        import traceback
        
        # Получаем или создаем пользователя с инициализацией полей
        try:
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
            logger.info(f"DEBUG: Пользователь {user_id} {'создан' if created else 'найден'}")
        except Exception as e:
            logger.error(f"Ошибка создания/получения пользователя {user_id}: {e}")
            logger.error(traceback.format_exc())
            return JsonResponse({'success': False, 'message': f'Ошибка создания пользователя: {str(e)}'}, status=500)
        
        # Если пользователь только что создан, инициализируем реферальный код
        if created:
            try:
                from .referral_services import ReferralService
                referral_service = ReferralService()
                result = referral_service.create_referral_code_sync(user_id)
                if not result.get('success'):
                    logger.warning(f"Не удалось создать реферальный код для пользователя {user_id}: {result.get('message')}")
            except Exception as e:
                logger.error(f"Ошибка создания реферального кода для пользователя {user_id}: {e}")
                logger.error(traceback.format_exc())
                # Не прерываем выполнение, продолжаем без реферального кода
        
        # Получаем статистику рефералов
        try:
            from .referral_services import ReferralService
            referral_service = ReferralService()
            referral_stats = referral_service.get_referral_stats_sync(user_id)
            
            # Проверяем, что статистика получена успешно
            if not referral_stats.get('success'):
                logger.warning(f"Не удалось получить статистику рефералов для пользователя {user_id}: {referral_stats.get('message')}")
                # Используем значения по умолчанию
                referral_stats = {
                    'success': True,
                    'referrals_count': 0,
                    'total_purchases': 0,
                    'total_revenue': 0,
                    'total_commission': 0,
                    'total_rewards': 0,
                    'referral_code': 'Не создан',
                    'commission_percent': 20
                }
        except Exception as e:
            logger.error(f"Ошибка получения статистики рефералов для пользователя {user_id}: {e}")
            logger.error(traceback.format_exc())
            # Используем значения по умолчанию
            referral_stats = {
                'success': True,
                'referrals_count': 0,
                'total_purchases': 0,
                'total_revenue': 0,
                'total_commission': 0,
                'total_rewards': 0,
                'referral_code': 'Не создан',
                'commission_percent': 20
            }
        
        # Формируем данные профиля
        try:
            profile_data = {
                'success': True,
                'user': {
                    'user_id': user.user_id,
                    'username': user.username or None,
                    'first_name': user.first_name or None,
                    'last_name': user.last_name or None,
                    'balance': float(user.balance),
                    'referral_balance': float(user.referral_balance),
                    'created_at': user.created_at.isoformat() if user.created_at else None
                },
                'referrals_count': referral_stats.get('referrals_count', 0),
                'total_purchases': referral_stats.get('total_purchases', 0),
                'total_revenue': referral_stats.get('total_revenue', 0),
                'total_commission': referral_stats.get('total_commission', 0),
                'total_rewards': referral_stats.get('total_rewards', 0),  # Для обратной совместимости
                'referral_code': referral_stats.get('referral_code', 'Не создан'),
                'commission_percent': referral_stats.get('commission_percent', 20)
            }
            
            logger.info(f"DEBUG: Профиль пользователя {user_id} успешно сформирован")
            return JsonResponse(profile_data)
        except Exception as e:
            logger.error(f"Ошибка формирования данных профиля для пользователя {user_id}: {e}")
            logger.error(traceback.format_exc())
            return JsonResponse({'success': False, 'message': f'Ошибка формирования данных: {str(e)}'}, status=500)
        
    except TelegramUser.DoesNotExist:
        logger.error(f"Пользователь {user_id} не найден")
        return JsonResponse({'success': False, 'message': 'Пользователь не найден'}, status=404)
    except Exception as e:
        logger.error(f"Ошибка получения профиля пользователя {user_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'message': f'Ошибка сервера: {str(e)}'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def create_balance_deposit(request):
    """Создать платеж для пополнения баланса"""
    try:
        from .models import TelegramUser, BalanceTransaction, Payment
        from .services import PaymentService
        
        # Проверяем тип данных
        if request.content_type == 'application/json':
            data = json.loads(request.body)
            user_id = data.get('user_id')
            amount = float(data.get('amount', 0))
        else:
            # Данные приходят как form-data
            user_id = request.POST.get('user_id')
            amount = float(request.POST.get('amount', 0))
        
        if not user_id or amount <= 0:
            return JsonResponse({'success': False, 'message': 'Неверные параметры'}, status=400)
        
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={
                'username': None,
                'first_name': None,
                'last_name': None,
                'multi_level_referral_enabled': False
            }
        )
        
        # Создаем транзакцию баланса
        with transaction.atomic():
            # Сначала создаем запись о платеже
            payment = Payment.objects.create(
                user=user,
                amount=amount,
                subscription_type='balance_deposit',
                status='pending'
            )
            
            balance_transaction = BalanceTransaction.objects.create(
                user=user,
                transaction_type='deposit',
                amount=amount,
                status='pending',
                description=f'Пополнение баланса на {amount} ₽',
                payment=payment  # Связываем сразу
            )
            
            # Создаем платеж через ЮKassa
            payment_service = PaymentService()
            payment_data = payment_service.create_yookassa_payment_sync(
                user_id=user_id,
                amount=amount,
                description=f'Пополнение баланса на {amount} ₽',
                return_url=f"https://t.me/webnetvpn_robot?start=balance_success",
                payment_id=payment.payment_id  # ✅ Передаем payment_id
            )
            
            if payment_data:
                # Обновляем запись о платеже с YooKassa ID
                payment.yookassa_payment_id = payment_data['payment_id']
                payment.save()
                
                return JsonResponse({
                    'success': True,
                    'payment_id': payment.payment_id,
                    'confirmation_url': payment_data['confirmation_url']
                })
            else:
                balance_transaction.status = 'cancelled'
                balance_transaction.save()
                return JsonResponse({'success': False, 'message': 'Ошибка создания платежа'}, status=500)
                
    except Exception as e:
        logger.error(f"Ошибка создания платежа для пополнения баланса: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def process_balance_payment(request):
    """Обработать успешное пополнение баланса"""
    try:
        from .models import Payment, BalanceTransaction
        
        data = json.loads(request.body)
        payment_id = data.get('payment_id')
        
        if not payment_id:
            return JsonResponse({'success': False, 'message': 'Не указан ID платежа'}, status=400)
        
        try:
            payment = Payment.objects.get(payment_id=payment_id)
            balance_transaction = BalanceTransaction.objects.get(payment=payment)
            
            with transaction.atomic():
                # Обновляем статус платежа
                payment.status = 'succeeded'
                payment.paid_at = timezone.now()
                payment.save()
                
                # Обновляем баланс пользователя (конвертируем amount в Decimal)
                from decimal import Decimal
                payment.user.balance += Decimal(str(balance_transaction.amount))
                payment.user.save()
                
                # Обновляем статус транзакции
                balance_transaction.status = 'completed'
                balance_transaction.completed_at = timezone.now()
                balance_transaction.save()
                
                return JsonResponse({'success': True, 'message': 'Баланс успешно пополнен'})
                
        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Платеж не найден'}, status=404)
        except BalanceTransaction.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Транзакция не найдена'}, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка обработки пополнения баланса: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def buy_subscription_with_balance(request):
    """Покупка подписки с баланса"""
    try:
        from .models import TelegramUser, BalanceTransaction, SubscriptionKey, Payment
        from .services import PaymentService
        
        data = json.loads(request.body)
        user_id = data.get('user_id')
        subscription_type = data.get('subscription_type')  # 'month' или 'year'
        price = float(data.get('price', 0))
        
        if not user_id or not subscription_type or price <= 0:
            return JsonResponse({'success': False, 'message': 'Неверные параметры'}, status=400)
        
        # Получаем пользователя
        try:
            user = TelegramUser.objects.get(user_id=user_id)
        except TelegramUser.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Пользователь не найден'}, status=404)
        
        # Проверяем баланс (конвертируем price в Decimal)
        from decimal import Decimal
        if user.balance < Decimal(str(price)):
            return JsonResponse({
                'success': False, 
                'message': 'Недостаточно средств на балансе',
                'current_balance': float(user.balance),
                'required_amount': price
            }, status=400)
        
        # Определяем тип подписки
        if subscription_type == 'trial':
            subscription_name = 'Пробная подписка'
            total_activations = 1
        elif subscription_type == 'week':
            subscription_name = 'Недельная подписка'
            total_activations = 1
        elif subscription_type == 'month':
            subscription_name = 'Месячная подписка'
            total_activations = 1
        elif subscription_type == '3months':
            subscription_name = 'Подписка на 3 месяца'
            total_activations = 1
        elif subscription_type == '6months':
            subscription_name = 'Подписка на 6 месяцев'
            total_activations = 1
        elif subscription_type == 'year':
            subscription_name = 'Годовая подписка'
            total_activations = 1
        else:
            return JsonResponse({'success': False, 'message': 'Неверный тип подписки'}, status=400)
        
        with transaction.atomic():
            # Списываем с баланса (конвертируем price в Decimal)
            from decimal import Decimal
            user.balance -= Decimal(str(price))
            user.save()
            
            # Создаем транзакцию списания
            balance_transaction = BalanceTransaction.objects.create(
                user=user,
                transaction_type='purchase',
                amount=price,
                status='completed',
                description=f'Покупка {subscription_name} с баланса',
                completed_at=timezone.now()
            )
            
            # Получаем доступный ключ из базы данных
            from django.db import models
            subscription_key = SubscriptionKey.objects.filter(
                subscription_type=subscription_type,
                is_active=True,
                used_activations__lt=models.F('total_activations')
            ).first()
            
            if not subscription_key:
                # Возвращаем средства на баланс при отсутствии ключей
                user.balance += Decimal(str(price))
                user.save()
                
                # Создаем транзакцию возврата
                BalanceTransaction.objects.create(
                    user=user,
                    transaction_type='refund',
                    amount=price,
                    status='completed',
                    description='Возврат при отсутствии доступных ключей',
                    completed_at=timezone.now()
                )
                
                return JsonResponse({
                    'success': False, 
                    'message': 'Нет доступных ключей для данного типа подписки',
                    'current_balance': float(user.balance)
                }, status=400)
            
            # Увеличиваем счетчик использованных активаций
            subscription_key.used_activations += 1
            if subscription_key.used_activations >= subscription_key.total_activations:
                subscription_key.is_active = False
            subscription_key.save()
            
            # Создаем запись в Payment для отслеживания ключа
            payment = Payment.objects.create(
                user=user,
                amount=int(price),
                status='succeeded',
                subscription_type=subscription_type,
                issued_key=subscription_key.key_value,
                paid_at=timezone.now()
            )
            
            # Обрабатываем реферальную награду (синхронно)
            try:
                from .referral_services import ReferralService
                from config import BOT_TOKEN
                from aiogram import Bot
                
                # Создаем фиктивный платеж для обработки реферала
                class MockPayment:
                    def __init__(self, user, amount):
                        self.user = user
                        self.amount = amount
                        self.subscription_type = subscription_type
                
                mock_payment = MockPayment(user, price)
                
                # Обрабатываем реферал синхронно
                referral_service = ReferralService()
                result = referral_service.process_referral_purchase_sync(user.user_id, mock_payment)
                logger.info(f"Результат обработки реферала: {result}")
                
            except Exception as e:
                logger.error(f"Ошибка обработки реферала: {e}")
            
            logger.info(f"Пользователь {user_id} купил {subscription_name} за {price} ₽ с баланса")
            
            return JsonResponse({
                'success': True,
                'issued_key': subscription_key.key_value,
                'new_balance': float(user.balance),
                'subscription_type': subscription_type,
                'total_activations': subscription_key.total_activations,
                'remaining_activations': subscription_key.remaining_activations
            })
            
    except Exception as e:
        logger.error(f"Ошибка покупки подписки с баланса: {e}")
        
        # Пытаемся вернуть средства при любой ошибке
        try:
            if 'user_id' in locals() and 'user' in locals() and 'price' in locals():
                from decimal import Decimal
                user.balance += Decimal(str(price))
                user.save()
                
                # Создаем транзакцию возврата
                BalanceTransaction.objects.create(
                    user=user,
                    transaction_type='refund',
                    amount=price,
                    status='completed',
                    description='Возврат при ошибке покупки',
                    completed_at=timezone.now()
                )
                
                logger.info(f"Средства возвращены пользователю {user_id}: {price} ₽")
        except Exception as refund_error:
            logger.error(f"Ошибка возврата средств: {refund_error}")
        
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)

@csrf_exempt
@require_http_methods(["GET"])
def get_balance_payment_status(request, payment_id):
    """Получить статус платежа пополнения баланса"""
    try:
        from .models import Payment, BalanceTransaction
        
        try:
            payment = Payment.objects.get(payment_id=payment_id, subscription_type='balance_deposit')
            balance_transaction = BalanceTransaction.objects.get(payment=payment)
            
            logger.info(f"DEBUG: Статус платежа {payment_id}: {payment.status}")
            
            return JsonResponse({
                'success': True,
                'status': payment.status,
                'subscription_type': payment.subscription_type,
                'amount': float(balance_transaction.amount),
                'new_balance': float(payment.user.balance),
                'payment_id': payment.payment_id,
                'yookassa_payment_id': payment.yookassa_payment_id,
                'created_at': payment.created_at.isoformat() if payment.created_at else None,
                'paid_at': payment.paid_at.isoformat() if payment.paid_at else None
            })
            
        except Payment.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Платеж не найден'}, status=404)
        except BalanceTransaction.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Транзакция не найдена'}, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка получения статуса платежа пополнения баланса: {e}")
        return JsonResponse({'success': False, 'message': 'Ошибка сервера'}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def manual_confirm_balance_payment(request, payment_id):
    """Ручное подтверждение платежа пополнения баланса (для тестирования)"""
    try:
        from .models import Payment, BalanceTransaction
        
        try:
            payment = Payment.objects.get(payment_id=payment_id, subscription_type='balance_deposit')
            balance_transaction = BalanceTransaction.objects.get(payment=payment)
            
            with transaction.atomic():
                # Обновляем статус платежа
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
                
                logger.info(f"DEBUG: Платеж {payment_id} успешно подтвержден вручную")
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
@require_http_methods(["POST"])
def refund_balance(request):
    """Возврат средств на баланс при ошибке покупки"""
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        amount = data.get('amount')
        reason = data.get('reason', 'Возврат при ошибке покупки')
        
        if not all([user_id, amount]):
            return JsonResponse({
                'success': False,
                'message': 'Не все обязательные поля заполнены'
            }, status=400)
        
        # Проверяем, что пользователь существует
        try:
            from .models import TelegramUser, BalanceTransaction
            user = TelegramUser.objects.get(user_id=user_id)
        except TelegramUser.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Пользователь не найден'
            }, status=404)
        
        # Возвращаем средства на баланс
        with transaction.atomic():
            # Увеличиваем баланс
            user.balance += amount
            user.save()
            
            # Создаем транзакцию возврата
            BalanceTransaction.objects.create(
                user=user,
                transaction_type='refund',
                amount=amount,
                status='completed',
                description=reason,
                completed_at=timezone.now()
            )
        
        logger.info(f"Возврат {amount} ₽ пользователю {user_id}: {reason}")
        
        return JsonResponse({
            'success': True,
            'message': f'Средства возвращены на баланс: {amount} ₽',
            'new_balance': float(user.balance)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Неверный формат JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Ошибка возврата средств: {e}")
        return JsonResponse({
            'success': False,
            'message': 'Внутренняя ошибка сервера'
        }, status=500)
