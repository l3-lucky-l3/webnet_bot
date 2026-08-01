import logging
from decimal import Decimal
from typing import Optional, Dict, Any
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotificationEventType, WebhookNotification
from django.conf import settings
from .models import Payment as PaymentModel
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_WEBHOOK_URL

logger = logging.getLogger(__name__)

# Настройка ЮKassa
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY


class YooKassaService:
    """Сервис для работы с ЮKassa API"""
    
    @staticmethod
    def create_payment(payment_model: PaymentModel, return_url: str = None) -> Optional[Dict[str, Any]]:
        """
        Создает платеж в ЮKassa
        
        Args:
            payment_model: Модель платежа из Django
            return_url: URL для возврата после оплаты
            
        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            # Создаем платеж в ЮKassa
            payment = Payment.create({
                "amount": {
                    "value": f"{payment_model.amount}.00",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url or f"https://t.me/your_bot?start=payment_{payment_model.payment_id}"
                },
                "capture": True,
                "description": f"Подписка {payment_model.get_subscription_type_display()} - {payment_model.user.username or payment_model.user.user_id}",
                "metadata": {
                    "payment_id": str(payment_model.payment_id),
                    "user_id": str(payment_model.user.user_id),
                    "subscription_type": payment_model.subscription_type
                }
            }, payment_model.payment_id)
            
            # Обновляем модель платежа
            payment_model.yookassa_payment_id = payment.id
            payment_model.yookassa_confirmation_url = payment.confirmation.confirmation_url
            payment_model.save()
            
            logger.info(f"Создан платеж ЮKassa {payment.id} для платежа {payment_model.payment_id}")
            
            return {
                "id": payment.id,
                "status": payment.status,
                "confirmation_url": payment.confirmation.confirmation_url,
                "amount": payment.amount.value,
                "currency": payment.amount.currency,
                "description": payment.description,
                "created_at": payment.created_at
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания платежа ЮKassa для {payment_model.payment_id}: {e}")
            return None
    
    @staticmethod
    def get_payment_status(payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает статус платежа из ЮKassa
        
        Args:
            payment_id: ID платежа в ЮKassa
            
        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            payment = Payment.find_one(payment_id)
            
            return {
                "id": payment.id,
                "status": payment.status,
                "amount": payment.amount.value,
                "currency": payment.amount.currency,
                "description": payment.description,
                "created_at": payment.created_at,
                "paid": payment.paid,
                "metadata": payment.metadata
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статуса платежа {payment_id}: {e}")
            return None
    
    @staticmethod
    def capture_payment(payment_id: str, amount: float = None) -> bool:
        """
        Подтверждает платеж в ЮKassa (capture)
        
        Args:
            payment_id: ID платежа в ЮKassa
            amount: Сумма для подтверждения (если None, подтверждается полная сумма)
            
        Returns:
            True если подтверждение успешно, False иначе
        """
        try:
            logger.info(f"DEBUG: Подтверждаем платеж {payment_id} в ЮKassa")
            
            # Сначала получаем информацию о платеже
            payment_info = Payment.find_one(payment_id)
            if not payment_info:
                logger.error(f"DEBUG: Платеж {payment_id} не найден в ЮKassa")
                return False
            
            # Определяем сумму для подтверждения
            if amount is None:
                capture_amount = payment_info.amount.value
            else:
                capture_amount = str(amount)
            
            logger.info(f"DEBUG: Подтверждаем сумму {capture_amount} для платежа {payment_id}")
            
            # Подтверждаем платеж с указанием суммы
            capture_data = {
                "amount": {
                    "value": capture_amount,
                    "currency": payment_info.amount.currency
                }
            }
            
            response = Payment.capture(payment_id, capture_data)
            
            if response and response.status == 'succeeded':
                logger.info(f"DEBUG: Платеж {payment_id} успешно подтвержден в ЮKassa")
                return True
            else:
                logger.warning(f"DEBUG: Платеж {payment_id} не подтвержден, статус: {response.status if response else 'None'}")
                return False
                
        except Exception as e:
            logger.error(f"DEBUG: Ошибка подтверждения платежа {payment_id}: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    @staticmethod
    def process_webhook(webhook_data: Dict[str, Any]) -> bool:
        """
        Обрабатывает webhook от ЮKassa
        
        Args:
            webhook_data: Данные webhook
            
        Returns:
            True если обработка успешна, False иначе
        """
        try:
            logger.info(f"DEBUG: Получен webhook от ЮKassa: {webhook_data}")
            
            # Получаем событие напрямую из данных
            event = webhook_data.get('event')
            logger.info(f"DEBUG: Событие webhook: {event}")
            
            # Получаем объект платежа
            payment_object = webhook_data.get('object', {})
            payment_id = payment_object.get('id')
            logger.info(f"DEBUG: ID платежа: {payment_id}")
            
            # Проверяем тип события
            if event == 'payment.succeeded':
                logger.info("DEBUG: Обрабатываем успешный платеж")
                result = YooKassaService._handle_payment_succeeded_simple(payment_object)
                logger.info(f"DEBUG: Результат обработки успешного платежа: {result}")
                return result
            elif event == 'payment.waiting_for_capture':
                logger.info("DEBUG: Обрабатываем платеж в ожидании подтверждения")
                result = YooKassaService._handle_payment_succeeded_simple(payment_object)
                logger.info(f"DEBUG: Результат обработки платежа в ожидании: {result}")
                return result
            elif event == 'payment.canceled':
                logger.info("DEBUG: Обрабатываем отмененный платеж")
                result = YooKassaService._handle_payment_canceled_simple(payment_object)
                logger.info(f"DEBUG: Результат обработки отмененного платежа: {result}")
                return result
            else:
                logger.warning(f"DEBUG: Неизвестный тип события webhook: {event}")
                return False
                
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки webhook: {e}")
            logger.error(f"DEBUG: Тип ошибки: {type(e)}")
            logger.error(f"DEBUG: Детали ошибки: {str(e)}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    @staticmethod
    def _handle_payment_succeeded_simple(payment_object: Dict[str, Any]) -> bool:
        """Простая обработка успешного платежа"""
        try:
            payment_id = payment_object.get('id')
            logger.info(f"DEBUG: Обрабатываем успешный платеж: {payment_id}")
            
            # Получаем метаданные
            metadata = payment_object.get('metadata', {})
            our_payment_id = metadata.get('payment_id')
            user_id = metadata.get('user_id')
            
            logger.info(f"DEBUG: Наш ID платежа: {our_payment_id}, ID пользователя: {user_id}")
            
            if not our_payment_id:
                logger.error("DEBUG: Не найден наш ID платежа в метаданных")
                return False
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_id
            ).first()
            
            if not payment_model:
                logger.error(f"DEBUG: Платеж {payment_id} не найден в БД")
                return False
            
            logger.info(f"DEBUG: Найден платеж в БД: {payment_model.payment_id}")
            
            # Обновляем статус платежа
            payment_model.status = 'succeeded'
            payment_model.save()
            
            logger.info(f"DEBUG: Статус платежа обновлен на 'succeeded'")
            
            # Если это платеж пополнения баланса, обрабатываем его
            if hasattr(payment_model, 'balancetransaction'):
                logger.info("DEBUG: Это платеж пополнения баланса")
                return YooKassaService._handle_balance_payment_success(payment_model)
            else:
                logger.info("DEBUG: Это обычный платеж подписки")
                return YooKassaService._handle_subscription_payment_success(payment_model)
                
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки успешного платежа: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    @staticmethod
    def _handle_payment_canceled_simple(payment_object: Dict[str, Any]) -> bool:
        """Простая обработка отмененного платежа"""
        try:
            payment_id = payment_object.get('id')
            logger.info(f"DEBUG: Обрабатываем отмененный платеж: {payment_id}")
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_id
            ).first()
            
            if not payment_model:
                logger.error(f"DEBUG: Платеж {payment_id} не найден в БД")
                return False
            
            # Обновляем статус платежа
            payment_model.status = 'canceled'
            payment_model.save()
            
            logger.info(f"DEBUG: Статус платежа обновлен на 'canceled'")
            return True
            
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки отмененного платежа: {e}")
            return False
    
    @staticmethod
    def _handle_payment_waiting_simple(payment_object: Dict[str, Any]) -> bool:
        """Простая обработка платежа в ожидании"""
        try:
            payment_id = payment_object.get('id')
            logger.info(f"DEBUG: Обрабатываем платеж в ожидании: {payment_id}")
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_id
            ).first()
            
            if not payment_model:
                logger.error(f"DEBUG: Платеж {payment_id} не найден в БД")
                return False
            
            # Обновляем статус платежа
            payment_model.status = 'waiting_for_capture'
            payment_model.save()
            
            logger.info(f"DEBUG: Статус платежа обновлен на 'waiting_for_capture'")
            return True
            
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки платежа в ожидании: {e}")
            return False
    
    @staticmethod
    def _handle_balance_payment_success(payment_model: PaymentModel) -> bool:
        """Обрабатывает успешное пополнение баланса"""
        try:
            from .models import BalanceTransaction, TelegramUser
            
            # Находим транзакцию баланса
            balance_transaction = BalanceTransaction.objects.filter(
                payment=payment_model
            ).first()
            
            if not balance_transaction:
                logger.error(f"DEBUG: Транзакция баланса не найдена для платежа {payment_model.payment_id}")
                return False
            
            # Обновляем статус транзакции
            balance_transaction.status = 'succeeded'
            balance_transaction.save()
            
            # Обновляем баланс пользователя
            user = payment_model.user
            user.balance += payment_model.amount
            user.save()
            
            logger.info(f"DEBUG: Баланс пользователя {user.user_id} пополнен на {payment_model.amount} ₽")
            logger.info(f"DEBUG: Новый баланс: {user.balance} ₽")
            
            # Отправляем уведомление пользователю
            try:
                from aiogram import Bot
                from config import BOT_TOKEN
                import asyncio
                
                # Отправляем уведомление через бота
                notification_message = f'✅ <b>Баланс пополнен!</b>\n\n💰 <b>Сумма:</b> {payment_model.amount} ₽\n💳 <b>Новый баланс:</b> {user.balance} ₽\n\n<i>Спасибо за пополнение! 🚀</i>'
                
                # Запускаем уведомление в новом event loop
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                bot = Bot(token=BOT_TOKEN)
                loop.run_until_complete(bot.send_message(chat_id=user.user_id, text=notification_message, parse_mode='HTML'))
                loop.run_until_complete(bot.session.close())
                
            except Exception as e:
                logger.error(f"DEBUG: Ошибка отправки уведомления: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки пополнения баланса: {e}")
            return False
    
    @staticmethod
    def _handle_subscription_payment_success(payment_model: PaymentModel) -> bool:
        """Обрабатывает успешную оплату подписки"""
        try:
            # Здесь можно добавить логику активации подписки
            logger.info(f"DEBUG: Подписка активирована для пользователя {payment_model.user.user_id}")
            return True
            
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки подписки: {e}")
            return False
    
    @staticmethod
    def _handle_payment_succeeded(notification: WebhookNotification) -> bool:
        """Обрабатывает успешный платеж"""
        try:
            payment_data = notification.object
            logger.info(f"Обрабатываем успешный платеж: {payment_data.id}")
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_data.id
            ).first()
            
            if not payment_model:
                logger.error(f"Платеж {payment_data.id} не найден в БД")
                return False
            
            logger.info(f"Найден платеж в БД: {payment_model.payment_id}, тип: {payment_model.subscription_type}")
            
            # Обновляем статус
            payment_model.status = 'succeeded'
            payment_model.paid_at = payment_data.created_at
            payment_model.save()
            
            # Проверяем тип платежа
            if payment_model.subscription_type == 'balance_deposit':
                logger.info("Обрабатываем пополнение баланса")
                # Обрабатываем пополнение баланса напрямую
                from .models import BalanceTransaction
                from django.utils import timezone
                from django.db import transaction
                
                try:
                    with transaction.atomic():
                        # Находим транзакцию баланса
                        balance_transaction = BalanceTransaction.objects.get(payment=payment_model)
                        
                        # Обновляем баланс пользователя
                        payment_model.user.balance += balance_transaction.amount
                        payment_model.user.save()
                        
                        # Обновляем статус транзакции
                        balance_transaction.status = 'completed'
                        balance_transaction.completed_at = timezone.now()
                        balance_transaction.save()
                        
                        logger.info(f"Баланс успешно пополнен для платежа {payment_data.id} на сумму {balance_transaction.amount}")
                        
                        # Отправляем уведомление пользователю через Telegram
                        try:
                            from .services import PaymentService
                            payment_service = PaymentService()
                            
                            success_text = f"""
✅ <b>Платеж подтвержден!</b>

💰 <b>Баланс пополнен на:</b> {balance_transaction.amount} ₽
💳 <b>Текущий баланс:</b> {payment_model.user.balance} ₽

<i>Спасибо за пополнение! 🚀</i>
"""
                            
                            # Отправляем уведомление пользователю
                            payment_service._send_telegram_message_sync(
                                user_id=payment_model.user.user_id,
                                message=success_text
                            )
                            
                            logger.info(f"Уведомление отправлено пользователю {payment_model.user.user_id}")
                            
                        except Exception as notify_e:
                            logger.error(f"Ошибка отправки уведомления пользователю: {notify_e}")
                        
                except BalanceTransaction.DoesNotExist:
                    logger.error(f"Транзакция баланса не найдена для платежа {payment_model.payment_id}")
                except Exception as e:
                    logger.error(f"Ошибка пополнения баланса для платежа {payment_data.id}: {e}")
            else:
                logger.info(f"Обрабатываем обычный платеж подписки: {payment_model.subscription_type}")
                # Выдаем ключ подписки
                from .services import PaymentService
                payment_service = PaymentService()
                payment_service.confirm_payment(payment_model)
            
            logger.info(f"Платеж {payment_data.id} успешно обработан")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа: {e}")
            return False
    
    @staticmethod
    def _handle_payment_canceled(notification: WebhookNotification) -> bool:
        """Обрабатывает отмененный платеж"""
        try:
            payment_data = notification.object
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_data.id
            ).first()
            
            if not payment_model:
                logger.error(f"Платеж {payment_data.id} не найден в БД")
                return False
            
            # Обновляем статус
            payment_model.status = 'canceled'
            payment_model.save()
            
            logger.info(f"Платеж {payment_data.id} отменен")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки отмененного платежа: {e}")
            return False
    
    @staticmethod
    def _handle_payment_waiting(notification: WebhookNotification) -> bool:
        """Обрабатывает платеж в ожидании"""
        try:
            payment_data = notification.object
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                yookassa_payment_id=payment_data.id
            ).first()
            
            if not payment_model:
                logger.error(f"Платеж {payment_data.id} не найден в БД")
                return False
            
            # Обновляем статус
            payment_model.status = 'pending'
            payment_model.save()
            
            logger.info(f"Платеж {payment_data.id} в ожидании")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки платежа в ожидании: {e}")
            return False
