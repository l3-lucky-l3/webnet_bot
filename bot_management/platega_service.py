import logging
import requests
from typing import Optional, Dict, Any
from django.conf import settings
from .models import Payment as PaymentModel
from config import PLATEGA_MERCHANT_ID, PLATEGA_SECRET

logger = logging.getLogger(__name__)

PLATEGA_BASE_URL = 'https://app.platega.io'


class PlategaService:
    """Сервис для работы с Platega API"""
    
    @staticmethod
    def create_payment(payment_model: PaymentModel, return_url: str = None, failed_url: str = None, payment_method: int = 2) -> Optional[Dict[str, Any]]:
        """
        Создает платеж в Platega

        Args:
            payment_model: Модель платежа из Django
            return_url: URL для возврата после успешной оплаты
            failed_url: URL для возврата после неудачной оплаты
            payment_method: Метод оплаты (2 - СБП QR, 11 - банковская карта, 13 - криптовалюта)

        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            # Проверяем credentials
            if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
                logger.error("PLATEGA_MERCHANT_ID или PLATEGA_SECRET не установлены в config.py")
                return None
            
            # Если return_url не передан, создаем ссылку на бота
            if not return_url:
                bot_username = "webnetvpn_robot"  # Имя бота
                return_url = f"https://t.me/{bot_username}?start=payment_success_{payment_model.payment_id}"
            
            if not failed_url:
                bot_username = "webnetvpn_robot"  # Имя бота
                failed_url = f"https://t.me/{bot_username}?start=payment_failed_{payment_model.payment_id}"
            
            # Определяем описание подписки
            subscription_names = {
                'week': '1 неделя (ОБХОД глушилок + VPN)',
                'month': 'Месячная подписка',
                '3months': '3 месяца',
                '6months': '6 месяцев',
                'year': 'Годовая подписка',
                'regular_day': '1 день (Обычный VPN)',
                'regular_month': '1 месяц (Обычный VPN)',
                'regular_3months': '3 месяца (Обычный VPN)',
                'regular_6months': '6 месяцев (Обычный VPN)',
                'regular_year': '1 год (Обычный VPN)',
                'regular_2years': '2 года (Обычный VPN)',
                'fast_week': '1 неделя (Обычный VPN)',
                'fast_month': '1 месяц (Обычный VPN)',
                'fast_3months': '3 месяца (Обычный VPN)',
                'fast_6months': '6 месяцев (Обычный VPN)',
                'fast_year': '1 год (Обычный VPN)',
            }
            description = f"Оплата подписки: {subscription_names.get(payment_model.subscription_type, payment_model.subscription_type)}"

            # Формируем payload для webhook
            import json as json_module
            payload = {
                'payment_id': str(payment_model.payment_id),
                'user_id': str(payment_model.user.user_id),
                'subscription_type': payment_model.subscription_type,
                'vpn_type': payment_model.vpn_type  # Добавляем тип VPN
            }
            
            # Данные для создания платежа
            payment_request = {
                'paymentMethod': payment_method,  # Метод оплаты (2 - СБП, 11 - карта, 13 - крипта)
                'paymentDetails': {
                    'amount': payment_model.amount,  # Сумма в рублях
                    'currency': 'RUB',
                },
                'description': description,
                'return': return_url,
                'failedUrl': failed_url,
                'payload': json_module.dumps(payload)  # JSON строка
            }
            
            logger.info(f"Создание платежа Platega для payment_id={payment_model.payment_id}, amount={payment_model.amount}")
            logger.debug(f"Payment request: {payment_request}")
            
            # Создаем платеж синхронно через requests
            headers = {
                'Content-Type': 'application/json',
                'X-MerchantId': PLATEGA_MERCHANT_ID,
                'X-Secret': PLATEGA_SECRET,
            }
            
            response = requests.post(
                f'{PLATEGA_BASE_URL}/transaction/process',
                json=payment_request,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"Platega API response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Platega API response: {result}")
                
                # Обновляем модель платежа
                transaction_id = result.get('transactionId')
                redirect_url = result.get('redirect')
                
                if not transaction_id or not redirect_url:
                    logger.error(f"Неполный ответ от Platega: transactionId={transaction_id}, redirect={redirect_url}")
                    return None
                
                payment_model.platega_transaction_id = transaction_id
                payment_model.platega_payment_url = redirect_url
                payment_model.save()
                
                logger.info(f"Создан платеж Platega {transaction_id} для платежа {payment_model.payment_id}")
                
                return {
                    'transactionId': transaction_id,
                    'redirect': redirect_url,
                    'status': result.get('status', 'PENDING'),
                    'amount': payment_model.amount,
                    'currency': 'RUB',
                    'description': description,
                }
            else:
                error_text = response.text
                logger.error(f"Platega API error: {response.status_code} - {error_text}")
                logger.error(f"Request headers: {headers}")
                logger.error(f"Request body: {payment_request}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка создания платежа Platega для {payment_model.payment_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    @staticmethod
    def get_payment_status(transaction_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает статус платежа из Platega API
        Согласно документации: https://docs.platega.io/проверка-статуса-оплаты-платежа-22645077e0
        
        Endpoint: GET /transaction/{id}
        Статусы: PENDING, CANCELED, CONFIRMED, CHARGEBACKED
        
        Args:
            transaction_id: ID транзакции (UUID) в Platega
            
        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            if not PLATEGA_MERCHANT_ID or not PLATEGA_SECRET:
                logger.error("PLATEGA_MERCHANT_ID или PLATEGA_SECRET не установлены")
                return None
            
            headers = {
                'X-MerchantId': PLATEGA_MERCHANT_ID,
                'X-Secret': PLATEGA_SECRET,
            }
            
            logger.info(f"DEBUG: Проверяем статус платежа Platega transaction_id={transaction_id}")
            response = requests.get(
                f'{PLATEGA_BASE_URL}/transaction/{transaction_id}',
                headers=headers,
                timeout=30
            )
            
            logger.info(f"DEBUG: Platega API response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"DEBUG: Platega API response: {result}")
                
                # Согласно документации, статус находится в поле 'status'
                # Возможные значения: PENDING, CANCELED, CONFIRMED, CHARGEBACKED
                status = result.get('status')
                if status:
                    status = status.upper()  # Нормализуем к верхнему регистру
                
                # Извлекаем данные согласно структуре ответа из документации
                payment_details = result.get('paymentDetails', {})
                
                return {
                    'id': result.get('id') or transaction_id,
                    'status': status,  # PENDING, CANCELED, CONFIRMED, CHARGEBACKED
                    'amount': payment_details.get('amount') or result.get('amount'),
                    'currency': payment_details.get('currency') or result.get('currency', 'RUB'),
                    'description': result.get('description'),
                    'paymentMethod': result.get('paymentMethod'),
                    'merchantName': result.get('merchantName'),
                    'externalId': result.get('externalId'),
                }
            elif response.status_code == 404:
                logger.warning(f"Транзакция {transaction_id} не найдена в Platega")
                return None
            else:
                error_text = response.text
                logger.error(f"Platega API error: {response.status_code} - {error_text}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения статуса платежа {transaction_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    @staticmethod
    def process_webhook(webhook_data: Dict[str, Any], merchant_id: str = None, secret: str = None, skip_notification: bool = False) -> bool:
        """
        Обрабатывает webhook от Platega
        Согласно документации: https://docs.platega.io/callback-об-изменении-статуса-транзакции-22645075e0
        
        Callback содержит:
        - id: UUID транзакции
        - amount: сумма
        - currency: валюта
        - status: CONFIRMED или CANCELED
        - paymentMethod: ID метода оплаты
        - payload: дополнительные данные
        
        Args:
            webhook_data: Данные webhook от Platega
            merchant_id: MerchantId из заголовка (для логирования)
            secret: Secret из заголовка (для логирования)
            skip_notification: Пропустить отправку уведомления (бот отправит сам)
            
        Returns:
            True если обработка успешна, False иначе
        """
        try:
            logger.info(f"DEBUG: Получен webhook от Platega: {webhook_data}")
            
            # Согласно документации, обязательные поля: id, amount, currency, status, paymentMethod
            transaction_id = webhook_data.get('id')
            status = webhook_data.get('status')
            amount = webhook_data.get('amount')
            currency = webhook_data.get('currency')
            payment_method = webhook_data.get('paymentMethod')
            payload = webhook_data.get('payload', '')
            
            # Валидация обязательных полей
            if not transaction_id or not status:
                logger.error(f"DEBUG: Недостаточно данных в webhook. transaction_id={transaction_id}, status={status}")
                return False
            
            # Нормализуем статус к верхнему регистру
            status = status.upper() if status else None
            
            logger.info(f"DEBUG: Transaction ID: {transaction_id}, Status: {status}, Amount: {amount}, Currency: {currency}")
            
            # Находим платеж в нашей БД
            payment_model = PaymentModel.objects.filter(
                platega_transaction_id=transaction_id
            ).first()
            
            if not payment_model:
                logger.warning(f"DEBUG: Платеж с transaction_id {transaction_id} не найден в БД")
                return False
            
            logger.info(f"DEBUG: Найден платеж в БД: payment_id={payment_model.payment_id}, user_id={payment_model.user.user_id}")
            
            # Маппинг статусов Platega -> наша система
            # Согласно документации: CONFIRMED (успешная оплата) или CANCELED (неуспешная)
            if status == 'CONFIRMED':
                # Проверяем, не был ли платеж уже обработан
                if payment_model.status == 'succeeded':
                    logger.info(f"DEBUG: Платеж {payment_model.payment_id} уже обработан")
                    return True

                # Платеж успешен в Platega - обрабатываем его автоматически
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} успешен в Platega (CONFIRMED), выдаем ключ")

                # Автоматически обрабатываем успешный платеж и выдаем ключ
                return PlategaService._handle_payment_success(payment_model, skip_notification=skip_notification)
                
            elif status == 'CANCELED':
                # Отменяем платеж
                if payment_model.status != 'canceled':
                    payment_model.status = 'canceled'
                    payment_model.save()
                    logger.info(f"DEBUG: Статус платежа {payment_model.payment_id} обновлен на 'canceled'")
                else:
                    logger.info(f"DEBUG: Платеж {payment_model.payment_id} уже был отменен")
                return True
            else:
                logger.warning(f"DEBUG: Неизвестный статус Platega: {status}")
                return False
                
        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки webhook: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False
    
    @staticmethod
    def _handle_payment_success(payment_model: PaymentModel, skip_notification: bool = False) -> bool:
        """Обрабатывает успешный платеж"""
        try:
            from .services import PaymentService

            logger.info(f"DEBUG: Обрабатываем успешный платеж: {payment_model.payment_id}")
            logger.info(f"DEBUG: payment.subscription_type={payment_model.subscription_type}, payment.vpn_type={payment_model.vpn_type}")

            # Проверяем тип VPN - для regular_* подписок всегда используем Remnawave API
            vpn_type = getattr(payment_model, 'vpn_type', 'night')
            subscription_type = payment_model.subscription_type

            # Если подписка начинается с 'regular_' или vpn_type='regular', используем Remnawave API
            is_regular_vpn = (vpn_type == 'regular') or subscription_type.startswith('regular_')
            is_fast_vpn = (vpn_type == 'fast') or subscription_type.startswith('fast_')

            if is_regular_vpn:
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} - Обычный VPN (regular_*), генерируем ключ через Remnawave")
                return PlategaService._handle_regular_vpn_payment_success(payment_model, skip_notification=skip_notification)
            elif is_fast_vpn:
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} - Обычный VPN, генерируем ключ через bypass API")
                payment_service = PaymentService()
                payment_service.confirm_payment(payment_model)
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} успешно обработан (Обычный VPN)")
                return True
            else:
                # Для Night VPN используем PaymentService
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} - Night VPN, выдаем ключ через PaymentService")
                payment_service = PaymentService()
                payment_service.confirm_payment(payment_model)
                logger.info(f"DEBUG: Платеж {payment_model.payment_id} успешно обработан")
                return True

        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки успешного платежа: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False

    @staticmethod
    def _handle_regular_vpn_payment_success(payment_model: PaymentModel, skip_notification: bool = False) -> bool:
        """
        Обрабатывает успешный платеж для Обычного VPN
        Генерирует ключ через Remnawave API
        """
        try:
            from .regular_vpn_service import process_regular_vpn_payment_success_sync

            logger.info(f"DEBUG: Обрабатываем успешный платеж Обычного VPN: {payment_model.payment_id}")
            logger.info(f"DEBUG: payment.subscription_type={payment_model.subscription_type}, vpn_type={payment_model.vpn_type}")

            # Вызываем синхронную функцию
            result = process_regular_vpn_payment_success_sync(payment_model.payment_id, skip_notification=skip_notification)

            logger.info(f"DEBUG: Результат генерации ключа: {result}")

            if result and result.get('success'):
                key = result.get('key')
                logger.info(f"DEBUG: Ключ для Обычного VPN успешно сгенерирован: {key[:20] if key else 'None'}...")

                # Обновляем платеж
                payment_model.issued_key = key
                payment_model.status = 'succeeded'
                from django.utils import timezone
                payment_model.paid_at = timezone.now()
                payment_model.save()

                return True
            else:
                error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Ошибка генерации ключа'
                logger.error(f"DEBUG: Ошибка генерации ключа для Обычного VPN: {error_msg}")
                logger.error(f"DEBUG: Полный результат: {result}")

                # Обновляем статус платежа, но помечаем что ключ не выдан
                payment_model.status = 'succeeded'
                from django.utils import timezone
                payment_model.paid_at = timezone.now()
                payment_model.save()

                return False

        except Exception as e:
            logger.error(f"DEBUG: Ошибка обработки успешного платежа Обычного VPN: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False

