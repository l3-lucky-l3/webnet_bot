"""
Упрощенный сервис для работы с платежами
Убираем сложную логику и делаем простую, надежную систему
"""
import logging
import requests
import json
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any
from django.conf import settings
from .models import Payment, BalanceTransaction, TelegramUser
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

logger = logging.getLogger(__name__)


class SimplePaymentService:
    """Упрощенный сервис для работы с платежами"""
    
    @staticmethod
    def create_payment(user_id: int, amount: float, description: str) -> Optional[Dict[str, Any]]:
        """
        Создает платеж в ЮKassa
        
        Args:
            user_id: ID пользователя
            amount: Сумма платежа
            description: Описание платежа
            
        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            # Создаем платеж в ЮKassa
            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/webnetvpn_robot?start=payment_success"
                },
                "capture": True,  # Сразу подтверждаем платеж
                "description": description,
                "metadata": {
                    "user_id": str(user_id),
                    "payment_type": "balance_deposit"
                }
            }
            
            url = "https://api.yookassa.ru/v3/payments"
            
            # Правильная авторизация для ЮKassa
            import base64
            auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json",
                "Idempotence-Key": str(uuid.uuid4())
            }
            
            logger.info(f"Создаем платеж ЮKassa: {amount} ₽ для пользователя {user_id}")
            
            response = requests.post(url, json=payment_data, headers=headers, timeout=30)
            
            logger.info(f"Ответ ЮKassa: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Платеж создан: {data['id']}")
                
                return {
                    'payment_id': data['id'],
                    'status': data['status'],
                    'confirmation_url': data['confirmation']['confirmation_url'],
                    'amount': data['amount']['value'],
                    'currency': data['amount']['currency']
                }
            else:
                logger.error(f"Ошибка создания платежа: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка создания платежа: {e}")
            return None
    
    @staticmethod
    def check_payment_status(payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет статус платежа в ЮKassa
        
        Args:
            payment_id: ID платежа в ЮKassa
            
        Returns:
            Dict с данными платежа или None при ошибке
        """
        try:
            url = f"https://api.yookassa.ru/v3/payments/{payment_id}"
            
            # Правильная авторизация
            import base64
            auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'payment_id': data['id'],
                    'status': data['status'],
                    'paid': data.get('paid', False),
                    'amount': data['amount']['value'],
                    'currency': data['amount']['currency'],
                    'created_at': data['created_at']
                }
            else:
                logger.error(f"Ошибка проверки платежа: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка проверки платежа: {e}")
            return None
    
    @staticmethod
    def process_payment_success(payment_id: str, user_id: int, amount: float) -> bool:
        """
        Обрабатывает успешный платеж
        
        Args:
            payment_id: ID платежа в ЮKassa
            user_id: ID пользователя
            amount: Сумма платежа
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            from django.db import transaction
            from django.utils import timezone
            
            with transaction.atomic():
                # Получаем или создаем пользователя
                user, created = TelegramUser.objects.get_or_create(
                    user_id=user_id,
                    defaults={
                        'username': None,
                        'first_name': None,
                        'last_name': None,
                        'multi_level_referral_enabled': False
                    }
                )
                
                # Создаем запись о платеже
                payment = Payment.objects.create(
                    user=user,
                    amount=amount,
                    subscription_type='balance_deposit',
                    status='succeeded',
                    yookassa_payment_id=payment_id,
                    paid_at=timezone.now()
                )

                # Для пополнения баланса напоминание не нужно
                
                # Создаем транзакцию баланса
                balance_transaction = BalanceTransaction.objects.create(
                    user=user,
                    transaction_type='deposit',
                    amount=amount,
                    status='completed',
                    description=f'Пополнение баланса на {amount} ₽',
                    payment=payment,
                    completed_at=timezone.now()
                )
                
                # Обновляем баланс пользователя
                user.balance += Decimal(str(amount))
                user.save()
                
                logger.info(f"Платеж {payment_id} успешно обработан. Баланс пользователя {user_id} пополнен на {amount} ₽")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка обработки платежа {payment_id}: {e}")
            return False

