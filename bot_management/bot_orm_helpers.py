"""
Безопасные функции для работы с базой данных в боте
Заменяют прямые SQL запросы на Django ORM
"""
import logging
from django.utils import timezone
from .models import SupportChat, SupportMessage, Payment, TelegramUser

logger = logging.getLogger(__name__)

def get_or_create_support_chat_safe(user_id):
    """Безопасное получение или создание чата поддержки"""
    try:
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={'multi_level_referral_enabled': False}
        )
        
        chat, created = SupportChat.objects.get_or_create(
            user=user,
            defaults={
                'status': 'open',
                'unread_user_messages': 0,
                'unread_admin_messages': 0
            }
        )
        
        return chat.chat_id
    except Exception as e:
        logger.error(f"Ошибка создания чата поддержки: {e}")
        return None

def save_support_message_safe(chat_id, sender, text, photo_file_id=None):
    """Безопасное сохранение сообщения поддержки"""
    try:
        from asgiref.sync import sync_to_async
        
        @sync_to_async
        def _save_message():
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            message = SupportMessage.objects.create(
                chat=chat,
                sender=sender,
                text=text,
                photo_file_id=photo_file_id,
                is_read=False
            )
            
            # Обновляем счетчик сообщений
            if sender == 'user':
                chat.unread_user_messages += 1
            else:
                chat.unread_admin_messages += 1
            
            chat.save()
            return message
        
        return _save_message()
    except SupportChat.DoesNotExist:
        logger.error(f"Чат поддержки {chat_id} не найден")
        return None
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения поддержки: {e}")
        return None

def get_payment_safe(payment_id):
    """Безопасное получение платежа"""
    try:
        return Payment.objects.get(payment_id=payment_id)
    except Payment.DoesNotExist:
        logger.error(f"Платеж {payment_id} не найден")
        return None
    except Exception as e:
        logger.error(f"Ошибка получения платежа {payment_id}: {e}")
        return None

def update_payment_status_safe(payment_id, status, issued_key=None):
    """Безопасное обновление статуса платежа"""
    try:
        payment = Payment.objects.get(payment_id=payment_id)
        payment.status = status
        if issued_key:
            payment.issued_key = issued_key
        payment.save()
        return True
    except Payment.DoesNotExist:
        logger.error(f"Платеж {payment_id} не найден")
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления платежа {payment_id}: {e}")
        return False

def get_user_safe(user_id):
    """Безопасное получение пользователя"""
    try:
        return TelegramUser.objects.get(user_id=user_id)
    except TelegramUser.DoesNotExist:
        logger.error(f"Пользователь {user_id} не найден")
        return None
    except Exception as e:
        logger.error(f"Ошибка получения пользователя {user_id}: {e}")
        return None

def create_payment_safe(user_id, amount, subscription_type, payment_method='card'):
    """Безопасное создание платежа"""
    try:
        user = get_user_safe(user_id)
        if not user:
            return None
        
        payment = Payment.objects.create(
            user=user,
            amount=amount,
            subscription_type=subscription_type,
            payment_method=payment_method,
            status='pending'
        )
        
        return payment
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        return None

def get_available_key_safe(subscription_type, vpn_type='night'):
    """Безопасное получение доступного ключа"""
    try:
        from .models import SubscriptionKey

        # Определяем тип ключа в зависимости от vpn_type
        if vpn_type == 'regular':
            if subscription_type == 'trial':
                key_type = 'regular_day'
            elif subscription_type == 'month':
                key_type = 'regular_month'
            elif subscription_type in ('3months', '6months', 'year'):
                key_type = 'regular_month'
            else:
                key_type = subscription_type
        else:
            key_type = 'month' if subscription_type in ('3months', '6months', 'year') else subscription_type

        key = SubscriptionKey.objects.filter(
            subscription_type=key_type,
            vpn_type=vpn_type,
            used_activations__lt=models.F('total_activations')
        ).first()

        if key:
            key.used_activations += 1
            if key.used_activations >= key.total_activations:
                key.is_active = False
            key.save()

        return key
    except Exception as e:
        logger.error(f"Ошибка получения ключа: {e}")
        return None

def update_user_balance_safe(user_id, amount, transaction_type='deposit'):
    """Безопасное обновление баланса пользователя"""
    try:
        from .models import BalanceTransaction
        from decimal import Decimal
        
        user = get_user_safe(user_id)
        if not user:
            return False
        
        # Обновляем баланс
        user.balance += Decimal(str(amount))
        user.save()
        
        # Создаем транзакцию
        BalanceTransaction.objects.create(
            user=user,
            transaction_type=transaction_type,
            amount=amount,
            status='completed',
            description=f'Обновление баланса через бота',
            completed_at=timezone.now()
        )
        
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления баланса пользователя {user_id}: {e}")
        return False


