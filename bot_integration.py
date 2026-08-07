"""
Модуль для интеграции Django админки с Telegram ботом
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from django.conf import settings
from django.http import JsonResponse
from aiogram import Bot
from aiogram.types import Message, CallbackQuery
from bot_management.models import (
    TelegramUser, Payment, SupportChat, SupportMessage, 
    SubscriptionKey, Broadcast
)
from bot_management.services import PaymentService, BroadcastService, SupportService
from config import ADMIN_IDS, SUPPORT_GROUP_ID

logger = logging.getLogger(__name__)


class BotIntegration:
    """Класс для интеграции бота с Django админкой"""
    
    def __init__(self):
        self.bot = Bot(token=settings.BOT_TOKEN)
        self.payment_service = PaymentService()
        self.broadcast_service = BroadcastService()
        self.support_service = SupportService()
    
    async def handle_new_user(self, user_data: Dict[str, Any]) -> bool:
        """Обработка нового пользователя - создает или обновляет данные"""
        try:
            from asgiref.sync import sync_to_async
            
            @sync_to_async
            def create_or_update_user():
                user_id = user_data['user_id']
                username = user_data.get('username')
                first_name = user_data.get('first_name')
                last_name = user_data.get('last_name')
                
                logger.info(f"DEBUG: Обработка пользователя {user_id}: username={username}, first_name={first_name}, last_name={last_name}")
                
                # Получаем или создаем пользователя
                user, created = TelegramUser.objects.get_or_create(
                    user_id=user_id,
                    defaults={
                        'username': username,
                        'first_name': first_name,
                        'last_name': last_name,
                    }
                )
                
                # ВАЖНО: Обновляем данные пользователя даже если он уже существует
                # Это нужно для обновления username, first_name, last_name если они изменились
                updated = False
                if not created:
                    # Пользователь уже существует, обновляем его данные
                    # Сравниваем с учетом None - если новое значение не None, обновляем
                    if username is not None and user.username != username:
                        user.username = username
                        updated = True
                        logger.info(f"DEBUG: Обновлен username для пользователя {user_id}: {user.username} -> {username}")
                    if first_name is not None and user.first_name != first_name:
                        user.first_name = first_name
                        updated = True
                        logger.info(f"DEBUG: Обновлен first_name для пользователя {user_id}: {user.first_name} -> {first_name}")
                    if last_name is not None and user.last_name != last_name:
                        user.last_name = last_name
                        updated = True
                        logger.info(f"DEBUG: Обновлен last_name для пользователя {user_id}: {user.last_name} -> {last_name}")
                    
                    if updated:
                        user.save()
                        logger.info(f"Обновлены данные пользователя {user_id}: username={user.username}, first_name={user.first_name}, last_name={user.last_name}")
                    else:
                        logger.info(f"Данные пользователя {user_id} не изменились: username={user.username}, first_name={user.first_name}")
                else:
                    logger.info(f"Создан новый пользователь {user_id}: username={user.username}, first_name={user.first_name}")
                
                return created or updated
            
            return await create_or_update_user()
        except Exception as e:
            logger.error(f"Ошибка создания/обновления пользователя: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def handle_new_payment(self, payment_data: Dict[str, Any]) -> Optional[int]:
        """Обработка нового платежа"""
        try:
            user = TelegramUser.objects.get(user_id=payment_data['user_id'])
            
            payment = Payment.objects.create(
                user=user,
                amount=payment_data['amount'],
                subscription_type=payment_data['subscription_type'],
                status='pending'
            )

            # Для платежей из интеграции напоминание тоже не нужно
            # так как они обрабатываются автоматически
            
            return payment.payment_id
        except Exception as e:
            logger.error(f"Ошибка создания платежа: {e}")
            return None
    
    async def handle_payment_receipt(self, payment_id: int, pdf_file_id: str) -> bool:
        """Обработка чека платежа"""
        try:
            payment = Payment.objects.get(payment_id=payment_id)
            payment.pdf_file_id = pdf_file_id
            payment.has_receipt = True
            payment.save()
            return True
        except Exception as e:
            logger.error(f"Ошибка обработки чека: {e}")
            return False
    
    async def handle_support_message(self, user_id: int, message_text: str) -> Optional[int]:
        """Обработка сообщения поддержки"""
        try:
            from asgiref.sync import sync_to_async
            
            print(f"DEBUG: Ищем пользователя {user_id} в Django")
            user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            print(f"DEBUG: Пользователь найден: {user.username or user.user_id}")
            
            # Создаем или получаем чат
            chat, created = await sync_to_async(SupportChat.objects.get_or_create)(
                user=user,
                status='open',
                defaults={'status': 'open'}
            )
            
            # Создаем сообщение
            await sync_to_async(SupportMessage.objects.create)(
                chat=chat,
                sender='user',
                text=message_text
            )
            
            # Обновляем счетчик непрочитанных сообщений для админа
            chat.unread_admin_messages += 1
            await sync_to_async(chat.save)()
            
            # Уведомления админам убраны - только в группу
            
            # Уведомляем группу поддержки
            if SUPPORT_GROUP_ID and self.bot:
                try:
                    admin_url = f"http://127.0.0.1:8123/bot_management/support/{chat.chat_id}/"
                    group_message = f"""
🚨 <b>Новое сообщение в поддержке!</b>

👤 <b>Пользователь:</b> @{user.username or user.first_name or 'Без имени'}
🆔 <b>ID:</b> {user_id}
📝 <b>Сообщение:</b> {message_text}

🔗 <b>Открыть в админке:</b> <a href="{admin_url}">Перейти к чату</a>
                    """
                    
                    await self.bot.send_message(
                        SUPPORT_GROUP_ID,
                        group_message,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления в группу: {e}")
            
            return chat.chat_id
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения поддержки: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    async def send_broadcast_to_user(self, user_id: int, message_text: str) -> bool:
        """Отправка рассылки пользователю"""
        try:
            await self.bot.send_message(user_id, message_text, parse_mode="HTML")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки рассылки пользователю {user_id}: {e}")
            return False
    
    async def send_payment_notification(self, user_id: int, payment: Payment, action: str) -> bool:
        """Отправка уведомления о платеже"""
        try:
            if action == 'confirmed':
                await self.payment_service._notify_user_payment_confirmed(payment, None)
            elif action == 'rejected':
                await self.payment_service._notify_user_payment_rejected(payment)
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о платеже: {e}")
            return False


# Глобальный экземпляр для использования в боте
bot_integration = BotIntegration()


# Функции для использования в существующем боте
async def notify_django_new_user(user_data: Dict[str, Any]):
    """Уведомление Django о новом пользователе"""
    await bot_integration.handle_new_user(user_data)


async def notify_django_new_payment(payment_data: Dict[str, Any]) -> Optional[int]:
    """Уведомление Django о новом платеже"""
    return await bot_integration.handle_new_payment(payment_data)


async def notify_django_payment_receipt(payment_id: int, pdf_file_id: str):
    """Уведомление Django о загруженном чеке"""
    await bot_integration.handle_payment_receipt(payment_id, pdf_file_id)


async def notify_django_support_message(user_id: int, message_text: str) -> Optional[int]:
    """Уведомление Django о сообщении поддержки"""
    return await bot_integration.handle_support_message(user_id, message_text)


async def send_broadcast_from_django(user_id: int, message_text: str) -> bool:
    """Отправка рассылки из Django"""
    return await bot_integration.send_broadcast_to_user(user_id, message_text)


async def create_platega_payment(user_id: int, subscription_type: str, return_url: str = None, amount: int = None, payment_method: int = 2, vpn_type: str = 'night') -> Optional[Dict[str, Any]]:
    """Создание платежа через Platega API"""
    try:
        import aiohttp
        import json

        # Определяем цену, если не передана
        if amount is None:
            # Получаем цену из базы данных через API
            try:
                import aiohttp
                api_url = 'http://127.0.0.1:8123/bot_management/api/prices/get/'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            prices = data.get('prices', {})
                            amount = prices.get(subscription_type, 0)
                        else:
                            # Fallback на config.py
                            from config import PRICES
                            amount = PRICES.get(subscription_type, 0)
            except Exception as e:
                logger.error(f"Ошибка получения цены из API: {e}")
                # Fallback на config.py
                from config import PRICES
                amount = PRICES.get(subscription_type, 0)

        # Данные для API
        data = {
            'user_id': user_id,
            'subscription_type': subscription_type,
            'amount': amount,
            'return_url': return_url,
            'payment_method': payment_method,
            'vpn_type': vpn_type  # Добавляем тип VPN
        }

        # URL API
        api_url = 'http://127.0.0.1:8123/bot_management/api/payments/create/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result['status'] == 'success':
                        # Адаптируем ответ для совместимости
                        return {
                            'payment_id': result.get('payment_id'),
                            'transaction_id': result.get('transaction_id'),
                            'confirmation_url': result.get('confirmation_url'),  # redirect от Platega
                            'amount': result.get('amount'),
                            'subscription_type': result.get('subscription_type')
                        }
                    else:
                        logger.error(f"Ошибка создания платежа: {result['message']}")
                        return None
                else:
                    logger.error(f"HTTP ошибка {response.status}")
                    return None

    except Exception as e:
        logger.error(f"Ошибка создания платежа Platega: {e}")
        return None

# Для обратной совместимости
async def create_yookassa_payment(user_id: int, subscription_type: str, return_url: str = None, amount: int = None, payment_method: int = 2) -> Optional[Dict[str, Any]]:
    """Создание платежа через Platega API (старое название для совместимости)"""
    return await create_platega_payment(user_id, subscription_type, return_url, amount, payment_method)


async def create_cryptobot_payment(user_id: int, subscription_type: str, amount: int = None, asset: str = 'USDT', vpn_type: str = 'night') -> Optional[Dict[str, Any]]:
    """Создание платежа через CryptoBot API"""
    try:
        import aiohttp
        import json

        # Определяем цену, если не передана
        if amount is None:
            try:
                api_url = 'http://127.0.0.1:8123/bot_management/api/prices/get/'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            prices = data.get('prices', {})
                            amount = prices.get(subscription_type, 0)
                            # Для regular_* подписок
                            if amount == 0 and subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = prices.get(regular_type, 0)
                        else:
                            from config import PRICES, REGULAR_VPN_PRICES
                            if subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = REGULAR_VPN_PRICES.get(regular_type, 0)
                            else:
                                amount = PRICES.get(subscription_type, 0)
            except Exception as e:
                logger.error(f"Ошибка получения цены из API: {e}")
                from config import PRICES, REGULAR_VPN_PRICES
                if subscription_type.startswith('regular_'):
                    regular_type = subscription_type.replace('regular_', '')
                    amount = REGULAR_VPN_PRICES.get(regular_type, 0)
                else:
                    amount = PRICES.get(subscription_type, 0)

        # Данные для API
        data = {
            'user_id': user_id,
            'subscription_type': subscription_type,
            'amount': amount,
            'asset': asset,
            'vpn_type': vpn_type
        }

        # URL API
        api_url = 'http://127.0.0.1:8123/bot_management/api/payments/create-cryptobot/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('status') == 'success':
                        return {
                            'payment_id': result.get('payment_id'),
                            'invoice_id': result.get('invoice_id'),
                            'confirmation_url': result.get('confirmation_url'),
                            'amount': result.get('amount'),
                            'asset': result.get('asset'),
                            'subscription_type': result.get('subscription_type')
                        }
                    else:
                        logger.error(f"Ошибка создания платежа: {result.get('message')}")
                        return None
                else:
                    logger.error(f"HTTP ошибка {response.status}")
                    return None

    except Exception as e:
        logger.error(f"Ошибка создания платежа CryptoBot: {e}")
        return None


async def create_antilopay_payment(user_id: int, subscription_type: str, return_url: str = None, amount: int = None, vpn_type: str = 'night', delay: int = 0) -> Optional[Dict[str, Any]]:
    """Создание платежа через Antilopay API (СБП)"""
    try:
        import aiohttp

        # Определяем цену, если не передана
        if amount is None:
            try:
                api_url = 'http://127.0.0.1:8123/bot_management/api/prices/get/'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            prices = data.get('prices', {})
                            amount = prices.get(subscription_type, 0)
                            if amount == 0 and subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = prices.get(regular_type, 0)
                            elif amount == 0 and subscription_type.startswith('fast_'):
                                fast_type = subscription_type.replace('fast_', '')
                                amount = prices.get(fast_type, 0)
                        else:
                            from config import PRICES, ULTRA_FAST_VPN_PRICES, FAST_VPN_PRICES
                            if subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = ULTRA_FAST_VPN_PRICES.get(regular_type, 0)
                            elif subscription_type.startswith('fast_'):
                                fast_type = subscription_type.replace('fast_', '')
                                amount = FAST_VPN_PRICES.get(fast_type, 0)
                            else:
                                amount = PRICES.get(subscription_type, 0)
            except Exception as e:
                logger.error(f"Ошибка получения цены из API: {e}")
                from config import PRICES, ULTRA_FAST_VPN_PRICES, FAST_VPN_PRICES
                if subscription_type.startswith('regular_'):
                    regular_type = subscription_type.replace('regular_', '')
                    amount = ULTRA_FAST_VPN_PRICES.get(regular_type, 0)
                elif subscription_type.startswith('fast_'):
                    fast_type = subscription_type.replace('fast_', '')
                    amount = FAST_VPN_PRICES.get(fast_type, 0)
                else:
                    amount = PRICES.get(subscription_type, 0)

        data = {
            'user_id': user_id,
            'subscription_type': subscription_type,
            'amount': amount,
            'vpn_type': vpn_type,
            'delay': delay,
        }

        api_url = 'http://127.0.0.1:8123/bot_management/api/payments/create-antilopay/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('status') == 'success':
                        return {
                            'payment_id': result.get('payment_id'),
                            'transaction_id': result.get('transaction_id'),
                            'confirmation_url': result.get('confirmation_url'),
                            'amount': result.get('amount'),
                            'subscription_type': result.get('subscription_type'),
                        }
                    else:
                        error_msg = result.get('message', 'Неизвестная ошибка')
                        logger.error(f"Antilopay API error: {result}")
                        return {'error': error_msg}
                else:
                    error_body = await response.text()
                    logger.error(f"Antilopay HTTP ошибка {response.status}: {error_body[:500]}")
                    return {'error': error_body[:300]}

    except Exception as e:
        logger.error(f"Ошибка создания платежа Antilopay: {e}")
        return None


async def create_referral_payment(user_id: int, subscription_type: str, return_url: str = None, amount: int = None, vpn_type: str = 'night') -> Optional[Dict[str, Any]]:
    """Создание платежа через реферальный баланс API"""
    try:
        import aiohttp

        # Определяем цену, если не передана
        if amount is None:
            try:
                api_url = 'http://127.0.0.1:8123/bot_management/api/prices/get/'
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            prices = data.get('prices', {})
                            amount = prices.get(subscription_type, 0)
                            if amount == 0 and subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = prices.get(regular_type, 0)
                            elif amount == 0 and subscription_type.startswith('fast_'):
                                fast_type = subscription_type.replace('fast_', '')
                                amount = prices.get(fast_type, 0)
                        else:
                            from config import PRICES, ULTRA_FAST_VPN_PRICES, FAST_VPN_PRICES
                            if subscription_type.startswith('regular_'):
                                regular_type = subscription_type.replace('regular_', '')
                                amount = ULTRA_FAST_VPN_PRICES.get(regular_type, 0)
                            elif subscription_type.startswith('fast_'):
                                fast_type = subscription_type.replace('fast_', '')
                                amount = FAST_VPN_PRICES.get(fast_type, 0)
                            else:
                                amount = PRICES.get(subscription_type, 0)
            except Exception as e:
                logger.error(f"Ошибка получения цены из API: {e}")
                from config import PRICES, ULTRA_FAST_VPN_PRICES, FAST_VPN_PRICES
                if subscription_type.startswith('regular_'):
                    regular_type = subscription_type.replace('regular_', '')
                    amount = ULTRA_FAST_VPN_PRICES.get(regular_type, 0)
                elif subscription_type.startswith('fast_'):
                    fast_type = subscription_type.replace('fast_', '')
                    amount = FAST_VPN_PRICES.get(fast_type, 0)
                else:
                    amount = PRICES.get(subscription_type, 0)

        data = {
            'user_id': user_id,
            'subscription_type': subscription_type,
            'amount': amount,
            'vpn_type': vpn_type,
        }

        api_url = 'http://127.0.0.1:8123/bot_management/api/payments/create-referral/'

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('status') == 'success':
                        return {
                            'payment_id': result.get('payment_id'),
                            'confirmation_url': result.get('confirmation_url', ''),
                            'amount': result.get('amount'),
                            'subscription_type': result.get('subscription_type'),
                        }
                    else:
                        error_msg = result.get('message', 'Неизвестная ошибка')
                        logger.error(f"Referral API error: {result}")
                        return {'error': error_msg}
                else:
                    error_body = await response.text()
                    logger.error(f"Referral HTTP ошибка {response.status}: {error_body[:500]}")
                    return {'error': error_body[:300]}

    except Exception as e:
        logger.error(f"Ошибка создания платежа Referral: {e}")
        return None
