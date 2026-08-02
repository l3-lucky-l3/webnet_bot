import asyncio
import html
import logging
from typing import Optional, List
from django.db import transaction, models
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum
from .models import Payment, SubscriptionKey, Broadcast, TelegramUser
from config import BOT_TOKEN, ADMIN_IDS
import os
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# Путь к изображению по умолчанию
DEFAULT_IMAGE_PATH = "images/hellonightvpn.png"

# Тексты для выдачи ключа — всего 2 сообщения (при наличии ключей)
KEY_DELIVERY_MESSAGE_1 = """🎉 <b>Оплата подтверждена!</b>

✅ <b>Подписка ОБХОД глушилок + VPN активирована</b>

🔑 <b>Ваш ключ:</b>
{issued_key}

📅 <b>Действует до:</b> {expiry_date}

<b>🔧 Как подключить?</b>
1. Нажмите кнопку ниже чтобы открыть ключ
2. Выберите приложение для подключения РЕКОМЕНДУЕМ INCY
3. Нажмите «Добавить подписку»

<i>Спасибо за покупку! 🚀</i>"""

KEY_DELIVERY_MESSAGE_2 = """📲Установка и настройка

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a> : https://incy.cc/

🙏УСТАНОВКА
1.Скачиваем приложение <a href="https://incy.cc/">INCY</a> ( есть в AppStore и PlayMarket)
2. Нажимаем ( +Добавить )
3. Вставляем ссылку ключа
 
ГОТОВО✅

⚠️<b>Условия использования</b>

· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐<b>Выбор сервера от ГЛУШИЛОК:</b>

· При глушении связи — выбирайте сервер с припиской <b>ОБХОД БЕЛЫХ СПИСКОВ

</b>❗️<b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI если хотите чтобы обход заработал ✅</b><b>
</b>
· Если интернет не глушат — используйте обычный VPN

🔒<b>Безопасность:</b>

· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть <b>заблокирован без возможности возврата средств</b>

⚙️<b>Решение небольших проблем</b>:

· Обновить конфигурацию ( кнопка правее названия “WebNet” )
· Запустить проверку пинга ( кнопка молнии, рядом с обновлением )
· Перезапустить приложение
· Включить/выключить VPN"""


class PaymentService:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)

    def confirm_payment(self, payment: Payment) -> bool:
        """Подтверждает платеж и выдает ключ"""
        logger.info(f"DEBUG: Начинаем подтверждение платежа {payment.payment_id} для пользователя {payment.user.user_id}, тип подписки: {payment.subscription_type}")

        vpn_type = getattr(payment, 'vpn_type', 'night')

        if payment.status == 'succeeded':
            if payment.issued_key:
                logger.info(f"DEBUG: Платеж {payment.payment_id} уже подтвержден (succeeded) с ключом, пропуск повторной отправки уведомлений")
                return True
            else:
                logger.warning(f"DEBUG: Платеж {payment.payment_id} в статусе succeeded но ключ не выдан. Выдаём ключ.")
        payment_confirmed = False

        # ===== ПРОВЕРКА: ЭТО ПРОДЛЕНИЕ? =====
        if getattr(payment, 'is_renewal', False) and payment.renewal_for_payment:
            return self._confirm_renewal_payment(payment)

        # Для ULTRA FAST VPN (бывший Regular VPN) используем Remnawave API
        if vpn_type == 'regular':
            try:
                logger.info(f"DEBUG: Обработка платежа ULTRA FAST VPN {payment.payment_id} через Remnawave API")
                from .regular_vpn_service import process_regular_vpn_payment_success_sync_with_retry

                result = process_regular_vpn_payment_success_sync_with_retry(payment.payment_id)

                if result and result.get('success'):
                    logger.info(f"DEBUG: Платеж ULTRA FAST VPN {payment.payment_id} успешно обработан")
                    payment.refresh_from_db()
                    self._save_profit(payment)
                    payment_confirmed = True
                else:
                    error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Remnawave API вернул пустой ответ'
                    logger.error(f"DEBUG: Ошибка обработки платежа ULTRA FAST VPN {payment.payment_id}: {error_msg}")
                    payment.status = 'failed'
                    payment.save()
                    self._send_api_error_notification(payment, error_msg)
                return payment_confirmed
            except Exception as e:
                logger.error(f"DEBUG: Исключение при обработке платежа ULTRA FAST VPN {payment.payment_id}: {e}")
                import traceback
                logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
                payment.status = 'failed'
                payment.save()
                self._send_api_error_notification(payment, str(e))
                return False

        # Для Обычный VPN используем bypass Remnawave API (1 squad)
        if vpn_type == 'fast':
            try:
                logger.info(f"DEBUG: Обработка платежа Обычный VPN {payment.payment_id} через Remnawave Bypass API")
                from .night_vpn_fgn_service import process_fast_vpn_payment_sync_with_retry

                result = process_fast_vpn_payment_sync_with_retry(payment.payment_id)

                if result and result.get('success'):
                    logger.info(f"DEBUG: Платеж Обычный VPN {payment.payment_id} успешно обработан")
                    payment.refresh_from_db()
                    self._save_profit(payment)
                    payment_confirmed = True
                    self._send_fast_vpn_key_notification(payment, result.get('key_value'))
                else:
                    error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Bypass API вернул пустой ответ'
                    logger.error(f"DEBUG: Ошибка обработки платежа Обычный VPN {payment.payment_id}: {error_msg}")
                    payment.status = 'failed'
                    payment.save()
                    self._send_api_error_notification(payment, error_msg)
                return payment_confirmed
            except Exception as e:
                logger.error(f"DEBUG: Исключение при обработке платежа Обычный VPN {payment.payment_id}: {e}")
                import traceback
                logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
                payment.status = 'failed'
                payment.save()
                self._send_api_error_notification(payment, str(e))
                return False

        # Для Night VPN (ОБХОД глушилок + VPN) используем Remnawave Bypass API
        use_bypass_api = self._should_use_bypass_api(payment)

        if use_bypass_api:
            try:
                logger.info(f"DEBUG: Обработка платежа Night VPN {payment.payment_id} через Remnawave Bypass API")
                from .night_vpn_fgn_service import process_night_vpn_payment_sync_with_retry

                result = process_night_vpn_payment_sync_with_retry(payment.payment_id)

                if result and result.get('success'):
                    logger.info(f"DEBUG: Платеж Night VPN {payment.payment_id} успешно обработан")
                    payment.refresh_from_db()
                    self._save_profit(payment)
                    self._send_night_vpn_key_notification(payment, result.get('key_value'))
                    payment_confirmed = True
                else:
                    error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Remnawave Bypass API вернул пустой ответ'
                    logger.error(f"DEBUG: Ошибка обработки платежа Night VPN {payment.payment_id}: {error_msg}")
                    payment.status = 'failed'
                    payment.save()
                    self._send_api_error_notification(payment, error_msg)
                    payment_confirmed = False
                return payment_confirmed
            except Exception as e:
                logger.error(f"DEBUG: Исключение при обработке платежа Night VPN {payment.payment_id}: {e}")
                import traceback
                logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
                payment.status = 'failed'
                payment.save()
                self._send_api_error_notification(payment, str(e))
                payment_confirmed = False
                return payment_confirmed
        else:
            logger.error(f"DEBUG: Remnawave Bypass API не настроен для Night VPN платежа {payment.payment_id}")
            payment.status = 'failed'
            payment.save()
            self._send_api_error_notification(payment, 'Remnawave Bypass API не настроен')
            return False

    def _confirm_renewal_payment(self, payment: Payment) -> bool:
        """
        Подтверждает платеж продления подписки.
        Продлевает существующий ключ через API вместо создания нового.
        """
        from .models import Payment

        original_payment = payment.renewal_for_payment
        vpn_type = payment.vpn_type
        user = payment.user

        logger.info(f"Продление подписки: платеж {payment.payment_id} для оригинального {original_payment.payment_id}")

        # Определяем количество месяцев для продления
        months_map = {
            'month': 1,
            '3months': 3,
            '6months': 6,
            'year': 12,
            'day': 1,
            '2years': 24,
        }
        months = months_map.get(payment.subscription_type, 1)

        if vpn_type == 'night':
            return self._confirm_renewal_payment_night(payment, original_payment, months)
        elif vpn_type == 'regular':
            return self._confirm_renewal_payment_regular(payment, original_payment, months)
        elif vpn_type == 'fast':
            return self._confirm_renewal_payment_fast(payment, original_payment, months)
        else:
            logger.error(f"Неизвестный тип VPN для продления: {vpn_type}")
            return False

    def _confirm_renewal_payment_night(self, payment: Payment, original_payment: Payment, months: int) -> bool:
        """Продление Night VPN через Remnawave Bypass API. Также продлевает Regular VPN ключ если он есть."""
        try:
            from .night_vpn_fgn_service import extend_bypass_subscription

            bypass_uuid = original_payment.bypass_remnawave_uuid

            if not bypass_uuid:
                latest_payment = Payment.objects.filter(
                    user=payment.user,
                    vpn_type='night',
                    status='succeeded',
                    is_fgn_key=True,
                    bypass_remnawave_uuid__isnull=False,
                ).order_by('-paid_at').first()

                if latest_payment:
                    bypass_uuid = latest_payment.bypass_remnawave_uuid
                    logger.info(f"Найден bypass UUID {bypass_uuid} из последнего платежа {latest_payment.payment_id}")
                else:
                    logger.error(f"Не найден bypass Remnawave UUID для продления платежа {payment.payment_id}")
                    payment.status = 'failed'
                    payment.save()
                    self._send_api_error_notification(payment, 'Не найден bypass UUID для продления')
                    return False

            duration_days = months * 30

            extended = extend_bypass_subscription(original_payment, duration_days)
            if not extended:
                logger.error(f"Не удалось продлить bypass подписку для платежа {payment.payment_id}")
                return False

            logger.info(f"Bypass подписка продлена на {duration_days} дней")

            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.issued_key = original_payment.issued_key
            payment.fgcn_key_id = original_payment.fgcn_key_id
            payment.fgcn_tg_id = payment.user.user_id
            payment.is_fgn_key = True
            payment.bypass_remnawave_uuid = bypass_uuid
            payment.save()

            if original_payment.subscription_expires_at:
                original_payment.subscription_expires_at += timedelta(days=duration_days)
                original_payment.current_key_expires_at = original_payment.subscription_expires_at
                original_payment.subscription_reminder_sent = False
                original_payment.subscription_reminder_1d_sent = False
                original_payment.expiry_reminder_sent = False
                original_payment.subscription_just_expired_notified = False
                original_payment.save()
                logger.info(f"Оригинальный платеж {original_payment.payment_id} продлен до {original_payment.subscription_expires_at}")

            import json
            linked_ids = json.loads(original_payment.fgcn_linked_payment_ids or '[]')
            linked_ids.append(payment.payment_id)
            original_payment.fgcn_linked_payment_ids = json.dumps(linked_ids)
            original_payment.save()

            # ===== ПРОДЛЕНИЕ REGULAR VPN КЛЮЧА =====
            regular_key_extended = False
            regular_key_value = None
            if original_payment.regular_vpn_key:
                regular_key_value = original_payment.regular_vpn_key
                regular_key_extended = self._extend_regular_vpn_key_on_renewal(original_payment, months)

            # Отправляем уведомление пользователю
            new_expiry = original_payment.subscription_expires_at
            if original_payment.regular_vpn_key:
                if regular_key_extended:
                    msg = f"""✅ <b>Подписка продлена!</b>

💳 Платеж #{payment.payment_id} подтвержден
📅 Новая дата окончания: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>

🛡️ <b>Ваш ключ ОБХОД глушилок + VPN:</b>
{original_payment.issued_key}

🌍 <b>Ваш ключ обычного VPN:</b>
{original_payment.regular_vpn_key}

<i>Оба ключа продлены! Спасибо за продление подписки! 🚀</i>"""
                else:
                    msg = f"""✅ <b>Подписка продлена!</b>

💳 Платеж #{payment.payment_id} подтвержден
📅 Новая дата окончания: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>

🛡️ <b>Ваш ключ ОБХОД глушилок + VPN:</b>
{original_payment.issued_key}

⚠️ <b>Ключ обычного VPN не удалось продлить автоматически</b>
Обратитесь в поддержку: @yamalube61

<i>Спасибо за продление подписки! 🚀</i>"""
            else:
                msg = f"""✅ <b>Подписка продлена!</b>

💳 Платеж #{payment.payment_id} подтвержден
📅 Новая дата окончания: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>

🔑 Ваш ключ: {original_payment.issued_key}

<i>Спасибо за продление подписки! 🚀</i>"""

            self._send_telegram_message_sync(payment.user.user_id, msg)

            return True

        except Exception as e:
            logger.error(f"Ошибка продления Night VPN: {e}")
            import traceback
            logger.error(traceback.format_exc())
            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.save()
            self._send_telegram_message_sync(
                payment.user.user_id,
                f"⚠️ <b>Подписка продлена, но произошла ошибка при обновлении ключа</b>\n\nОбратитесь в поддержку: @yamalube61"
            )
            return True

    def _extend_regular_vpn_key_on_renewal(self, original_payment: Payment, months: int) -> bool:
        """
        Продлевает Regular VPN ключ при продлении Night VPN подписки.
        Просто обновляет expireAt существующего пользователя в Remnawave.
        """
        try:
            from .remnawave_api import get_remnawave_client
            from .models import SubscriptionKey
            from datetime import datetime, timedelta
            import asyncio

            night_sub_to_days = {
                'trial': 1,
                'week': 7,
                'month': 30,
                '3months': 90,
                '6months': 180,
                'year': 365,
                '2years': 730,
            }
            duration_days = night_sub_to_days.get(original_payment.subscription_type, 30)

            # Используем UUID из Payment или ищем в SubscriptionKey
            regular_vpn_uuid = original_payment.regular_vpn_remnawave_uuid

            if not regular_vpn_uuid and original_payment.regular_vpn_key:
                # Fallback: ищем UUID в SubscriptionKey
                sub_key = SubscriptionKey.objects.filter(
                    key_value=original_payment.regular_vpn_key
                ).first()
                if sub_key:
                    regular_vpn_uuid = sub_key.remnawave_user_uuid
                    logger.info(f"DEBUG: Найден UUID {regular_vpn_uuid} в SubscriptionKey для платежа {original_payment.payment_id}")

            if not regular_vpn_uuid:
                logger.error(f"DEBUG: Нет regular_vpn_remnawave_uuid для платежа {original_payment.payment_id}")
                return False

            remnawave_client = get_remnawave_client()
            if not remnawave_client:
                logger.error("DEBUG: Remnawave API клиент не инициализирован")
                return False

            # Получаем пользователя Remnawave по UUID
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                user = loop.run_until_complete(
                    remnawave_client.get_user_by_uuid(regular_vpn_uuid)
                )
            finally:
                loop.close()

            if not user:
                logger.error(f"DEBUG: Пользователь Remnawave с UUID {regular_vpn_uuid} не найден")
                return False

            # Продлеваем expireAt
            current_expire = user.get('expireAt')
            if current_expire:
                try:
                    current_expire_dt = datetime.fromisoformat(current_expire.replace('Z', '+00:00'))
                    new_expire_dt = current_expire_dt + timedelta(days=duration_days)
                except Exception as e:
                    logger.error(f"DEBUG: Ошибка парсинга expireAt: {e}")
                    new_expire_dt = datetime.now(tz=timezone.utc) + timedelta(days=duration_days)
            else:
                new_expire_dt = datetime.now(tz=timezone.utc) + timedelta(days=duration_days)

            new_expire_at = new_expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{new_expire_dt.microsecond // 1000:03d}Z"

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    remnawave_client._request('PATCH', '/api/users', {
                        'uuid': user.get('uuid'),
                        'expireAt': new_expire_at,
                        'status': 'ACTIVE'
                    })
                )
            finally:
                loop.close()

            logger.info(f"DEBUG: Regular VPN подписка продлена на {duration_days} дней, результат: {result}")
            return True

        except Exception as e:
            logger.error(f"DEBUG: Исключение при продлении Regular VPN: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _confirm_renewal_payment_regular(self, payment: Payment, original_payment: Payment, months: int) -> bool:
        """Продление Regular VPN через Remnawave API."""
        try:
            from .remnawave_api import RemnawaveAPI

            api = RemnawaveAPI()
            days_map = {'day': 1, 'month': 30, '3months': 90, '6months': 180, 'year': 365, '2years': 730}
            days = days_map.get(payment.subscription_type, 30)

            # Пробуем продлить существующую подписку
            extend_response = api.extend_subscription(payment.user.user_id, days)
            logger.info(f"Remnawave extend response: {extend_response}")

            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.issued_key = original_payment.issued_key
            payment.save()

            # Обновляем оригинальный платеж
            if original_payment.subscription_expires_at:
                original_payment.subscription_expires_at += timedelta(days=days)
                original_payment.current_key_expires_at = original_payment.subscription_expires_at
                original_payment.subscription_reminder_sent = False
                original_payment.subscription_reminder_1d_sent = False
                original_payment.expiry_reminder_sent = False
                original_payment.subscription_just_expired_notified = False
                original_payment.save()

            new_expiry = original_payment.subscription_expires_at
            msg = f"""✅ <b>Подписка продлена!</b>

💳 Платеж #{payment.payment_id} подтвержден
📅 Новая дата окончания: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>

🔑 Ваш ключ: {original_payment.issued_key}"""
            self._send_telegram_message_sync(payment.user.user_id, msg)

            return True

        except Exception as e:
            logger.error(f"Ошибка продления ULTRA FAST VPN: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _confirm_renewal_payment_fast(self, payment: Payment, original_payment: Payment, months: int) -> bool:
        """Продление Обычный VPN через Remnawave Bypass API."""
        try:
            from .night_vpn_fgn_service import extend_bypass_subscription

            bypass_uuid = original_payment.bypass_remnawave_uuid

            if not bypass_uuid:
                logger.error(f"Нет bypass_remnawave_uuid у Обычный VPN платежа {original_payment.payment_id}")
                return False

            days_map = {'week': 7, 'month': 30, '3months': 90, '6months': 180, 'year': 365}
            duration_days = days_map.get(payment.subscription_type, 30)

            extended = extend_bypass_subscription(original_payment, duration_days)
            if not extended:
                logger.error(f"Не удалось продлить Обычный VPN подписку для платежа {payment.payment_id}")
                return False

            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.issued_key = original_payment.issued_key
            payment.bypass_remnawave_uuid = bypass_uuid
            payment.is_fgn_key = True
            payment.save()

            if original_payment.subscription_expires_at:
                original_payment.subscription_expires_at += timedelta(days=duration_days)
                original_payment.current_key_expires_at = original_payment.subscription_expires_at
                original_payment.subscription_reminder_sent = False
                original_payment.subscription_reminder_1d_sent = False
                original_payment.expiry_reminder_sent = False
                original_payment.subscription_just_expired_notified = False
                original_payment.save()

            new_expiry = original_payment.subscription_expires_at
            msg = f"""✅ <b>Подписка продлена!</b>

💳 Платеж #{payment.payment_id} подтвержден
📅 Новая дата окончания: <b>{new_expiry.strftime('%d.%m.%Y %H:%M')}</b>

🔑 Ваш ключ: {original_payment.issued_key}"""
            self._send_telegram_message_sync(payment.user.user_id, msg)

            return True

        except Exception as e:
            logger.error(f"Ошибка продления Обычный VPN: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _should_use_bypass_api(self, payment: Payment) -> bool:
        """
        Определяет, использовать ли Remnawave Bypass API для данного платежа.
        Проверяет наличие настроек bypass Remnawave.
        """
        from config import REMNAWAVE_BYPASS_API_KEY

        if not REMNAWAVE_BYPASS_API_KEY:
            logger.info(f"Remnawave Bypass API не настроен (нет токена), используем пул ключей")
            return False

        try:
            from .models import BotSettings
            use_bypass = BotSettings.get_setting('night_vpn_use_fgn_api', 'true').lower() == 'true'
            if use_bypass:
                logger.info(f"Настроен Remnawave Bypass API для Night VPN")
            else:
                logger.info(f"Настроен старый метод (пул ключей) для Night VPN")
            return use_bypass
        except Exception as e:
            logger.warning(f"Ошибка проверки настройки Bypass API: {e}, используем API по умолчанию")
            return True

    def _confirm_payment_with_key_pool(self, payment: Payment) -> bool:
        """
        Старый метод подтверждения платежа через пул ключей из базы.
        """
        from django.db import transaction
        
        payment_confirmed = False
        
        try:
            with transaction.atomic():
                logger.info(f"DEBUG: Входим в транзакцию для платежа {payment.payment_id} (Night VPN, пул ключей)")
                
                # Выдаем ключ
                logger.info(f"DEBUG: Ищем доступный ключ для типа {payment.subscription_type}, vpn_type=night")
                key = self.get_available_key(payment.subscription_type, vpn_type='night')
                logger.info(f"DEBUG: Найден ключ: {key.key_value if key else 'None'}")
                if key:
                    logger.info(f"DEBUG: Выдаем ключ {key.key_value} для платежа {payment.payment_id}")
                    payment.issued_key = key.key_value
                    payment.status = 'succeeded'
                    payment.paid_at = timezone.now()
                    payment.subscription_expires_at = self._calculate_subscription_expiry(payment.subscription_type, payment.paid_at)
                    
                    if payment.subscription_type in ('3months', '6months', 'year'):
                        payment.current_key_expires_at = payment.paid_at + timedelta(days=30)
                    else:
                        payment.current_key_expires_at = payment.subscription_expires_at
                    
                    payment.save()
                    logger.info(f"DEBUG: Платеж {payment.payment_id} сохранен со статусом succeeded")

                    # Обновляем ключ
                    key.used_activations += 1
                    if key.used_activations >= key.total_activations:
                        key.is_active = False
                    key.save()
                    logger.info(f"DEBUG: Ключ {key.key_value} обновлен, использований: {key.used_activations}/{key.total_activations}")

                    # Проверяем, остались ли мало ключей
                    key_type = 'month' if payment.subscription_type in ('3months', '6months', 'year') else payment.subscription_type
                    self._check_low_key_count(key_type)

                    # Уведомляем пользователя
                    try:
                        logger.info(f"DEBUG: Отправляем уведомления для платежа {payment.payment_id} (Night VPN)")
                        self._notify_user_payment_confirmed_sync(payment, key)
                        logger.info(f"DEBUG: Уведомление отправлено для платежа {payment.payment_id}")
                    except Exception as e:
                        logger.error(f"DEBUG: Ошибка отправки уведомления для платежа {payment.payment_id}: {e}")

                    payment_confirmed = True
                    logger.info(f"DEBUG: Платеж {payment.payment_id} успешно подтвержден с ключом")
                else:
                    logger.warning(f"DEBUG: Ключи закончились для типа подписки {payment.subscription_type}")
                    payment.status = 'succeeded'
                    payment.paid_at = timezone.now()
                    payment.subscription_expires_at = self._calculate_subscription_expiry(payment.subscription_type, payment.paid_at)
                    if payment.subscription_type in ('3months', '6months', 'year'):
                        payment.current_key_expires_at = payment.paid_at + timedelta(days=30)
                    else:
                        payment.current_key_expires_at = payment.subscription_expires_at
                    payment.save()
                    
                    try:
                        self._notify_user_no_keys_sync(payment)
                    except Exception as e:
                        logger.error(f"DEBUG: Ошибка отправки уведомления об отсутствии ключей: {e}")
                    payment_confirmed = True

        except Exception as e:
            logger.error(f"Ошибка подтверждения платежа {payment.payment_id}: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False

        # Обрабатываем реферальную покупку
        if payment_confirmed:
            try:
                from .referral_services import ReferralService
                referral_service = ReferralService(bot=self.bot)
                result = referral_service.process_referral_purchase_sync(payment.user.user_id, payment)
                logger.info(f"Результат обработки реферала для платежа {payment.payment_id}: {result}")
            except Exception as e:
                logger.error(f"Ошибка обработки реферальной покупки для платежа {payment.payment_id}: {e}")
                import traceback
                logger.error(traceback.format_exc())

        return payment_confirmed

    def renew_multimonth_key(self, payment: Payment) -> bool:
        """
        Продлевает ключ для подписки на 3/6 месяцев/год: выдаёт новый месячный ключ.
        Вызывается, когда current_key_expires_at прошла, а subscription_expires_at ещё нет.
        
        ПРИМЕЧАНИЕ: Для FGN API ключей это не требуется — они создаются сразу на весь срок.
        """
        if payment.subscription_type not in ('3months', '6months', 'year'):
            return False

        # Remnawave Bypass API ключи создаются на весь срок — продление не требуется
        if getattr(payment, 'is_fgn_key', False) and getattr(payment, 'bypass_remnawave_uuid', None):
            logger.info(f"Bypass ключ для платежа {payment.payment_id} — продление не требуется (ключ на весь срок)")
            return True

        # Старый метод: из пула ключей
        vpn_type = getattr(payment, 'vpn_type', 'night')
        key_type = 'regular_month' if vpn_type == 'regular' else 'month'
        key = self.get_available_key(key_type, vpn_type=vpn_type)
        if not key:
            logger.warning(f"Нет доступных месячных ключей для продления платежа {payment.payment_id}")
            return False
        try:
            with transaction.atomic():
                payment.issued_key = key.key_value
                if payment.current_key_expires_at:
                    payment.current_key_expires_at = payment.current_key_expires_at + timedelta(days=30)
                else:
                    payment.current_key_expires_at = timezone.now() + timedelta(days=30)
                payment.save()
                key.used_activations += 1
                if key.used_activations >= key.total_activations:
                    key.is_active = False
                key.save()
            # Уведомляем пользователя о новом ключе
            try:
                msg = f"""🔄 <b>Продление подписки</b>

📅 Ваш новый ключ на следующий месяц:

{key.key_value}

Подписка действует до {payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else '—'}."""
                self._send_telegram_message_sync(payment.user.user_id, msg)
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления о продлении платежа {payment.payment_id}: {e}")
            return True
        except Exception as e:
            logger.error(f"Ошибка продления ключа для платежа {payment.payment_id}: {e}")
            return False

    def _issue_regular_vpn_key_for_night_payment(self, payment: Payment, night_key: str = None) -> bool:
        """
        Выдаёт ключ обычного VPN при покупке Night VPN (ОБХОД глушилок + VPN).
        Создаёт ключ через Remnawave API с тем же сроком действия.
        Отправляет комбинированное сообщение с обоими ключами.
        """
        try:
            logger.info(f"DEBUG: Выдача ключа обычного VPN для Night VPN платежа {payment.payment_id}")

            night_sub_to_regular_sub = {
                'trial': 'regular_day',
                'day': 'regular_day',
                'month': 'regular_month',
                '3months': 'regular_3months',
                '6months': 'regular_6months',
                'year': 'regular_year',
                '2years': 'regular_2years',
            }
            regular_subscription_type = night_sub_to_regular_sub.get(payment.subscription_type, 'regular_month')

            days_map = {
                'regular_day': 1,
                'regular_month': 30,
                'regular_3months': 90,
                'regular_6months': 180,
                'regular_year': 365,
                'regular_2years': 730,
            }
            duration_days = days_map.get(regular_subscription_type, 30)

            from .models import Payment as PaymentModel, TelegramUser

            regular_user, _ = TelegramUser.objects.get_or_create(
                user_id=payment.user.user_id,
                defaults={'username': payment.user.username or f'user_{payment.user.user_id}'}
            )

            regular_payment = PaymentModel.objects.create(
                user=regular_user,
                vpn_type='regular',
                subscription_type=regular_subscription_type,
                amount=0,
                status='pending'
            )

            logger.info(f"DEBUG: Создан временный платёж обычного VPN {regular_payment.payment_id}")

            from .regular_vpn_service import process_regular_vpn_payment_success_sync

            result = process_regular_vpn_payment_success_sync(regular_payment.payment_id, skip_notification=True)

            if result and result.get('success'):
                regular_key = result.get('key')
                logger.info(f"DEBUG: Ключ обычного VPN успешно сгенерирован: {regular_key[:50] if regular_key else 'None'}...")

                # Получаем UUID пользователя для продления
                regular_vpn_uuid = None
                if regular_payment.payment_id:
                    from .models import SubscriptionKey
                    sub_key = SubscriptionKey.objects.filter(key_value=regular_key).first()
                    if sub_key:
                        regular_vpn_uuid = sub_key.remnawave_user_uuid

                payment.regular_vpn_key = regular_key
                payment.regular_vpn_payment_id = regular_payment.payment_id
                payment.regular_vpn_remnawave_uuid = regular_vpn_uuid
                payment.save()

                # Отправляем комбинированное сообщение с обоими ключами
                self._send_combined_vpn_keys_notification(payment, regular_key, duration_days, night_key=night_key)

                return True
            else:
                error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Remnawave API вернул пустой ответ'
                logger.error(f"DEBUG: Ошибка генерации ключа обычного VPN: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"DEBUG: Исключение при выдаче ключа обычного VPN: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return False

    def _send_combined_vpn_keys_notification(self, payment: Payment, regular_key: str, duration_days: int, night_key: str = None):
        """Отправляет комбинированное сообщение с обоими ключами VPN"""
        try:
            from datetime import timedelta
            from .night_vpn_fgn_service import send_key_to_user_sync

            if not night_key:
                night_key = payment.issued_key

            expires_date = (payment.paid_at + timedelta(days=duration_days)).strftime('%d.%m.%Y') if payment.paid_at else '—'

            combined_message = f"""✅ <b>Оплата подтверждена!</b>

🛡️ <b>Ваш ключ ОБХОД глушилок + VPN:</b>
{night_key}

🌍 <b>Ваш ключ обычного VPN:</b>
{regular_key}

📅 <b>Оба ключа действуют до:</b> {expires_date}

<i>Спасибо за покупку! 🚀</i>

🔑 <b>Как подключить устройство?</b>

📲 Настройка:
1. Скачайте приложение <b>"Happ"</b>
2. Нажмите на кнопку ключа ниже(или ссылку выше) — он откроется в браузере
3. Приложение автоматически предложит добавить подписку
4. Готово!

⚠️ <b>Условия использования</b>
· Доступ на 1 устройство для каждого ключа
· При нарушении правил — бан без возврата средств

🔧 <b>Решение проблем:</b>
· Обновить конфигурацию (кнопка в правом верхнем углу)
· Запустить проверку пинга
· Перезапустить приложение
· Включить/выключить VPN"""

            send_key_to_user_sync(payment.user.user_id, combined_message, regular_vpn_key=regular_key, night_key=night_key)
            logger.info(f"DEBUG: Комбинированное уведомление отправлено пользователю {payment.user.user_id}")

        except Exception as e:
            logger.error(f"DEBUG: Ошибка отправки комбинированного уведомления: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def reject_payment(self, payment: Payment) -> bool:
        """Отклоняет платеж"""
        try:
            payment.status = 'rejected'
            payment.save()
            asyncio.create_task(self._notify_user_payment_rejected(payment))
            return True
        except Exception as e:
            logger.error(f"Ошибка отклонения платежа {payment.payment_id}: {e}")
            return False

    def get_available_key(self, subscription_type: str, vpn_type: str = 'night') -> Optional[SubscriptionKey]:
        """Получает доступный ключ для подписки. Для 3 месяцев, 6 месяцев и года берём ключ из общей месячной базы."""
        # Общая база: все ключи месячные; для 3months, 6months и year выдаём месячный ключ
        # Для обычного VPN используем соответствующие типы ключей
        if vpn_type == 'regular':
            # Обычный VPN: используем regular_* типы ключей
            if subscription_type == 'trial':
                key_type = 'regular_day'  # Пробный ключ для обычного VPN = 1 день
            elif subscription_type == 'month':
                key_type = 'regular_month'
            elif subscription_type == '3months':
                key_type = 'regular_month'  # Выдаём месячный ключ
            elif subscription_type == '6months':
                key_type = 'regular_month'  # Выдаём месячный ключ
            elif subscription_type == 'year':
                key_type = 'regular_month'  # Выдаём месячный ключ
            else:
                key_type = subscription_type
        else:
            # Night VPN: используем обычные типы
            key_type = 'month' if subscription_type in ('3months', '6months', 'year') else subscription_type
        
        return SubscriptionKey.objects.filter(
            subscription_type=key_type,
            vpn_type=vpn_type,
            used_activations__lt=models.F('total_activations')
        ).only('key_id', 'key_value', 'subscription_type', 'total_activations', 'used_activations', 'is_active', 'vpn_type').first()

    def _calculate_subscription_expiry(self, subscription_type: str, paid_at) -> Optional[timezone.datetime]:
        """Вычисляет дату окончания подписки на основе типа подписки"""
        if not paid_at:
            return None

        if subscription_type == 'trial':
            return paid_at + timedelta(days=1)
        elif subscription_type == 'week':
            return paid_at + timedelta(days=7)
        elif subscription_type == 'month':
            return paid_at + timedelta(days=30)
        elif subscription_type == '3months':
            return paid_at + timedelta(days=90)
        elif subscription_type == '6months':
            return paid_at + timedelta(days=180)
        elif subscription_type == 'year':
            return paid_at + timedelta(days=365)
        else:
            # Для пожизненных подписок возвращаем None
            return None

    def _check_low_key_count(self, subscription_type: str):
        """Проверяет количество доступных ключей и уведомляет админов при низком количестве"""
        try:
            from .models import SubscriptionKey

            # Получаем количество доступных ключей для данного типа
            available_count = SubscriptionKey.objects.filter(
                subscription_type=subscription_type,
                used_activations__lt=models.F('total_activations')
            ).aggregate(
                total_available=Sum('total_activations') - Sum('used_activations')
            )['total_available'] or 0

            # Если осталось меньше 2 ключей, уведомляем админов
            if available_count < 2:
                import asyncio
                asyncio.create_task(self._notify_admins_low_keys(subscription_type, available_count))

        except Exception as e:
            logger.error(f"Ошибка проверки количества ключей: {e}")

    async def _notify_admins_low_keys(self, subscription_type: str, available_count: int):
        """Уведомляет админов о низком количестве ключей"""
        try:
            from config import ADMIN_IDS

            # Определяем название типа подписки
            sub_names = {
                'week': 'Недельная',
                'month': 'Месячная',
                '3months': '3 месяца',
                '6months': '6 месяцев',
                'year': 'Годовая'
            }
            sub_name = sub_names.get(subscription_type, subscription_type)

            message = f"""
⚠️ <b>ВНИМАНИЕ! Низкий запас ключей</b>

📅 <b>Тип подписки:</b> {sub_name}
🔢 <b>Доступных ключей:</b> <code>{available_count}</code>

❌ <b>Рекомендуется пополнить склад ключей!</b>

💡 <i>Ключи заканчиваются, это может привести к проблемам с продажами.</i>
"""

            # Отправляем уведомление всем админам
            for admin_id in ADMIN_IDS:
                try:
                    await self.bot.send_message(
                        admin_id,
                        message,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

            logger.info(f"Отправлено уведомление админам: осталось {available_count} ключей типа {subscription_type}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админам о низком запасе ключей: {e}")

    def _save_profit(self, payment: Payment):
        """Рассчитывает и сохраняет чистую прибыль для платежа"""
        try:
            from config import ULTRA_FAST_VPN_PROFIT

            vpn_type = getattr(payment, 'vpn_type', 'night')
            sub_type = payment.subscription_type

            if vpn_type == 'regular':
                sub = sub_type.replace('regular_', '')
                payment.profit = ULTRA_FAST_VPN_PROFIT.get(sub, 0)
            elif vpn_type == 'fast':
                payment.profit = payment.amount
            elif vpn_type == 'night':
                payment.profit = payment.amount
            else:
                payment.profit = payment.amount

            payment.save(update_fields=['profit'])
            logger.info(f"Прибыль для платежа {payment.payment_id}: {payment.profit}₽ (vpn={vpn_type}, sub={sub_type})")
        except Exception as e:
            logger.error(f"Ошибка расчёта прибыли для платежа {payment.payment_id}: {e}")

    def _send_night_vpn_key_notification(self, payment: Payment, key_value: str = None):
        """Отправляет уведомление с ключом ОБХОД глушилок + VPN пользователю"""
        try:
            if not key_value:
                key_value = payment.issued_key

            expires_date = payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else '—'

            message = f"""🎉 <b>Оплата подтверждена!</b>

✅ <b>Подписка ОБХОД глушилок + VPN активирована</b>

🔑 <b>Ваш ключ:</b>
{key_value}

📅 <b>Действует до:</b> {expires_date}

<b>🔧 Как подключить?</b>
1. Нажмите кнопку ниже чтобы открыть ключ
2. Выберите приложение для подключения РЕКОМЕНДУЕМ INCY
3. Нажмите «Добавить подписку»

<i>Спасибо за покупку! 🚀</i>"""

            import json
            keyboard_buttons = [
                [{"text": "🛡️ Открыть ключ ОБХОД глушилок + VPN", "url": key_value}],
                [{"text": "⬅️ Главное меню", "callback_data": "main_menu"}],
            ]
            keyboard = {"inline_keyboard": keyboard_buttons}

            self._send_telegram_message_sync(payment.user.user_id, message)

            import time
            time.sleep(2)

            troubleshooting_msg = """📲Установка и настройка

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a> : https://incy.cc/

🙏УСТАНОВКА
1.Скачиваем приложение <a href="https://incy.cc/">INCY</a> ( есть в AppStore и PlayMarket)
2. Нажимаем ( +Добавить )
3. Вставляем ссылку ключа
 
ГОТОВО✅

⚠️<b>Условия использования</b>

· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐<b>Выбор сервера от ГЛУШИЛОК:</b>

· При глушении связи — выбирайте сервер с припиской <b>ОБХОД БЕЛЫХ СПИСКОВ

</b>❗️<b>ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI если хотите чтобы обход заработал ✅</b><b>
</b>
· Если интернет не глушат — используйте обычный VPN

🔒<b>Безопасность:</b>

· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть <b>заблокирован без возможности возврата средств</b>

⚙️<b>Решение небольших проблем</b>:

· Обновить конфигурацию ( кнопка правее названия “WebNet” )
· Запустить проверку пинга ( кнопка молнии, рядом с обновлением )
· Перезапустить приложение
· Включить/выключить VPN"""

            from pathlib import Path
            photo_path = Path("images/instruction.jpg")
            if photo_path.exists():
                from config import BOT_TOKEN
                import requests as req
                tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                with open(photo_path, 'rb') as f:
                    req.post(tg_url, files={'photo': f}, data={
                        'chat_id': payment.user.user_id,
                        'caption': troubleshooting_msg,
                        'parse_mode': 'HTML'
                    }, timeout=10)
                time.sleep(1)
                kb_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

            else:
                from config import BOT_TOKEN
                import requests as req
                req.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
                    'chat_id': payment.user.user_id,
                    'text': troubleshooting_msg,
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps(keyboard)
                }, timeout=5)

            logger.info(f"Уведомление Night VPN отправлено пользователю {payment.user.user_id}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления Night VPN пользователю {payment.user.user_id}: {e}")

    def _send_fast_vpn_key_notification(self, payment: Payment, key_value: str = None):
        """Отправляет уведомление с ключом Обычный VPN пользователю"""
        try:
            if not key_value:
                key_value = payment.issued_key

            expires_date = payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else '—'

            message = f"""✅ Оплата подтверждена!

🚀 Обычный VPN - Ключ активирован

🔑 Ваш ключ доступа:
{key_value}

📅 Действует до: {expires_date}

📲Установка и настройка

Мы рекомендуем это приложение👇
INCY (https://incy.cc/) : https://incy.cc/

🙏УСТАНОВКА
1.Скачиваем приложение INCY (https://incy.cc/) ( есть в AppStore и PlayMarket)
2. Нажимаем ( +Добавить )
3. Вставляем ссылку ключа
 
ГОТОВО✅

⚠️Условия использования

· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🌐Выбор сервера от ГЛУШИЛОК:

· При глушении связи — выбирайте сервер с припиской ОБХОД БЕЛЫХ СПИСКОВ

❗️ОБЯЗАТЕЛЬНО ВЫКЛЮЧАЙТЕ WI-FI если хотите чтобы обход заработал ✅

· Если интернет не глушат — используйте обычный VPN

🔒Безопасность:

· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть заблокирован без возможности возврата средств

⚙️Решение небольших проблем:

· Обновить конфигурацию ( кнопка правее названия "WebNet" )
· Запустить проверку пинга ( кнопка молнии, рядом с обновлением )
· Перезапустить приложение
· Включить/выключить VPN"""

            import json
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📲 Настройка (INCY)", "url": "https://incy.cc/"}],
                    [{"text": "🔑 Мои ключи", "callback_data": "my_keys"}],
                    [{"text": "📞 Поддержка", "callback_data": "support"}]
                ]
            }

            from config import BOT_TOKEN
            import requests as req
            req.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
                'chat_id': payment.user.user_id,
                'text': message,
                'reply_markup': json.dumps(keyboard)
            }, timeout=5)
            
            logger.info(f"Уведомление Обычный VPN отправлено пользователю {payment.user.user_id}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления Обычный VPN пользователю {payment.user.user_id}: {e}")

    async def _notify_user_payment_confirmed(self, payment: Payment, key: SubscriptionKey):
        """Уведомляет пользователя о подтверждении платежа — всего 2 сообщения."""
        try:
            from pathlib import Path
            from aiogram.types import FSInputFile

            logger.info(f"DEBUG: Отправка 2 сообщений для платежа {payment.payment_id}, пользователь {payment.user.user_id}")

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu"),
                 InlineKeyboardButton(text="💬 Написать менеджеру", url="https://t.me/yamalube61")]
            ])

            # Сообщение 1: подтверждение + ключ + полная инструкция + кнопки (ключ экранируем для HTML)
            expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M') if payment.subscription_expires_at else '—'
            text1 = KEY_DELIVERY_MESSAGE_1.format(issued_key=html.escape(key.key_value), expiry_date=expiry_date)
            await self.bot.send_message(payment.user.user_id, text1, parse_mode="HTML", reply_markup=keyboard)
            await asyncio.sleep(2)

            # Сообщение 2: фото с инструкцией
            photo_path = Path("images/instruction.jpg")
            if photo_path.exists():
                await self.bot.send_photo(payment.user.user_id, FSInputFile(photo_path), caption=KEY_DELIVERY_MESSAGE_2, parse_mode="HTML")
                await asyncio.sleep(1)

            else:
                await self.bot.send_message(payment.user.user_id, KEY_DELIVERY_MESSAGE_2, parse_mode="HTML", reply_markup=keyboard)

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {payment.user.user_id}: {e}")

    def _notify_user_payment_confirmed_sync(self, payment: Payment, key: SubscriptionKey):
        """Синхронная версия: 2 сообщения — подтверждение+инструкция, затем 2 фото + кнопки."""
        try:
            import requests
            import json
            from pathlib import Path

            logger.info(f"DEBUG: Синхронная отправка 2 сообщений для платежа {payment.payment_id}, пользователь {payment.user.user_id}")

            keyboard = {
                "inline_keyboard": [[
                    {"text": "⬅️ Главное меню", "callback_data": "main_menu"},
                    {"text": "💬 Написать менеджеру", "url": "https://t.me/yamalube61"}
                ]]
            }

            # Сообщение 1: подтверждение + ключ + полная инструкция + кнопки (ключ экранируем для HTML)
            expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M') if payment.subscription_expires_at else '—'
            text1 = KEY_DELIVERY_MESSAGE_1.format(issued_key=html.escape(key.key_value), expiry_date=expiry_date)
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(url, data={'chat_id': payment.user.user_id, 'text': text1, 'parse_mode': 'HTML', 'reply_markup': json.dumps(keyboard)}, timeout=5)
            import time
            time.sleep(2)

            # Сообщение 2: фото с инструкцией
            photo_path = Path("images/instruction.jpg")
            if photo_path.exists():
                tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                with open(photo_path, 'rb') as f:
                    requests.post(tg_url, files={'photo': f}, data={
                        'chat_id': payment.user.user_id,
                        'caption': KEY_DELIVERY_MESSAGE_2,
                        'parse_mode': 'HTML'
                    }, timeout=10)
                import time
                time.sleep(1)
                msg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

            else:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
                    'chat_id': payment.user.user_id,
                    'text': KEY_DELIVERY_MESSAGE_2,
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps(keyboard)
                }, timeout=5)

        except Exception as e:
            logger.error(f"Ошибка синхронной отправки уведомления пользователю {payment.user.user_id}: {e}")

    def _send_telegram_message_sync(self, user_id: int, message: str, image_path: str = None, reply_markup: str = None):
        """Синхронная отправка сообщения в Telegram с возможностью добавления изображения и клавиатуры"""
        try:
            import requests
            import json
            from pathlib import Path

            if image_path and Path(image_path).exists():
                # Отправляем фото с подписью (оптимизированно)
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

                with open(image_path, 'rb') as photo:
                    files = {'photo': photo}
                    data = {
                        'chat_id': user_id,
                        'caption': message,
                        'parse_mode': 'HTML',
                        'disable_notification': False  # Ускоряем отправку
                    }

                    if reply_markup:
                        data['reply_markup'] = reply_markup

                    # Уменьшаем timeout для быстрой отправки
                    response = requests.post(url, files=files, data=data, timeout=5)
            else:
                # Отправляем обычное текстовое сообщение
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                data = {
                    'chat_id': user_id,
                    'text': message,
                    'parse_mode': 'HTML',
                    'disable_notification': False
                }

                if reply_markup:
                    data['reply_markup'] = reply_markup

                response = requests.post(url, data=data, timeout=5)

            if response.status_code == 200:
                logger.info(f"DEBUG: Сообщение отправлено пользователю {user_id}")
            else:
                logger.error(f"DEBUG: Ошибка отправки сообщения пользователю {user_id}: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"DEBUG: Ошибка отправки сообщения пользователю {user_id}: {e}")

    def _create_inline_keyboard_sync(self, buttons: list) -> str:
        """Создает JSON строку для inline клавиатуры для синхронной отправки"""
        import json
        keyboard = {"inline_keyboard": buttons}
        return json.dumps(keyboard)

    async def _notify_user_no_keys(self, payment: Payment):
        """Уведомляет пользователя о том, что ключи закончились"""
        try:
            message = """
❌ <b>Ключи временно закончились</b>

🔧 <b>Что происходит:</b>
• Ваш платеж подтвержден
• Ключи для вашего типа подписки закончились
• Администратор скоро пополнит склад

⏰ <b>Время ожидания:</b> обычно в течение 1-2 часов

<i>Мы свяжемся с вами, как только ключи появятся</i>
"""

            # Создаем клавиатуру с кнопками
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                [InlineKeyboardButton(text="👨‍💼 Написать менеджеру", url="https://t.me/yamalube61")]
            ])

            await self.bot.send_message(payment.user.user_id, message, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о нехватке ключей {payment.user.user_id}: {e}")

    def _notify_user_no_keys_sync(self, payment: Payment):
        """Синхронная версия уведомления пользователя о том, что ключи закончились"""
        try:
            # Определяем тип подписки для сообщения
            sub_type_names = {
                'month': 'месячной',
                '3months': '3-месячной',
                '6months': '6-месячной',
                'year': 'годовой'
            }
            sub_type_text = sub_type_names.get(payment.subscription_type, payment.subscription_type)
            
            message = f"""
❌ <b>Ключи временно закончились</b>

🔧 <b>Что происходит:</b>
• Ваш платеж подтвержден ✅
• Ключи для {sub_type_text} подписки временно закончились
• Обратитесь в поддержку для получения ключа

📞 <b>Обратитесь в поддержку:</b>
• Напишите менеджеру: @yamalube61
• Укажите номер платежа: <b>#{payment.payment_id}</b>
• Скиньте чек об оплате
• Тип подписки: {sub_type_text}

⏰ <b>Время обработки:</b> обычно в течение 1-2 часов

<i>Мы свяжемся с вами, как только ключ будет готов</i>
"""

            # Создаем клавиатуру с кнопками
            keyboard_buttons = [
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}],
                [{"text": "👨‍💼 Написать менеджеру", "url": "https://t.me/yamalube61"}]
            ]
            reply_markup = self._create_inline_keyboard_sync(keyboard_buttons)

            logger.info(f"DEBUG: Отправляем синхронное уведомление об отсутствии ключей для платежа {payment.payment_id}, пользователь {payment.user.user_id}")
            self._send_telegram_message_sync(payment.user.user_id, message, DEFAULT_IMAGE_PATH, reply_markup)
            logger.info(f"DEBUG: Синхронное уведомление об отсутствии ключей отправлено для платежа {payment.payment_id}")
        except Exception as e:
            logger.error(f"Ошибка синхронной отправки уведомления о нехватке ключей {payment.user.user_id}: {e}")

    async def _notify_user_payment_rejected(self, payment: Payment):
        """Уведомляет пользователя об отклонении платежа"""
        try:
            message = """
❌ <b>Платеж отклонен</b>

🔍 <b>Возможные причины:</b>
• Неверная сумма перевода
• Неправильный получатель
• Некорректный формат чека
• Проблемы с качеством документа

🛠 <b>Что делать:</b>
1️⃣ Проверьте данные перевода
2️⃣ Убедитесь, что сумма точно совпадает
3️⃣ Свяжитесь с поддержкой через кнопку "🛠 Поддержка"
4️⃣ При необходимости сделайте новый перевод

<i>Мы поможем решить проблему! 💬</i>
"""
            await self.bot.send_message(payment.user.user_id, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об отклонении {payment.user.user_id}: {e}")

    def _send_api_error_notification(self, payment: Payment, error_msg: str):
        """Уведомляет пользователя об ошибке API при выдаче ключа"""
        try:
            vpn_labels = {
                'night': 'ОБХОД глушилок + VPN',
                'regular': 'ULTRA FAST VPN',
                'fast': 'Обычный VPN',
            }
            vpn_label = vpn_labels.get(payment.vpn_type, 'VPN')
            
            message = f"""⚠️ <b>Временная ошибка сервиса {vpn_label}</b>

🔧 <b>Что произошло:</b>
• Ваш платеж #{payment.payment_id} принят
• Сервер выдачи ключей временно недоступен
• Средства сохранены и будут использованы при восстановлении

📞 <b>Что делать:</b>
• Попробуйте получить ключ позже через "Моя подписка"
• Напишите менеджеру: @yamalube61
• Укажите номер платежа: <b>#{payment.payment_id}</b>

⏰ <b>Время восстановления:</b> обычно 5-15 минут

<i>Приносим извинения за неудобства! 🙏</i>
"""

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Повторить получение ключа", callback_data=f"retry_key_{payment.payment_id}")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
                [InlineKeyboardButton(text="👨‍💼 Написать менеджеру", url="https://t.me/yamalube61")]
            ])

            self._send_telegram_message_sync(payment.user.user_id, message, reply_markup=keyboard)
            logger.info(f"DEBUG: Уведомление об ошибке API отправлено пользователю {payment.user.user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке API {payment.user.user_id}: {e}")

    def create_yookassa_payment_sync(self, user_id: int, amount: float, description: str, return_url: str, payment_id: int = None):
        """Создание платежа через ЮKassa (синхронная версия)"""
        try:
            import requests
            import uuid
            
            # Получаем настройки ЮKassa
            from django.conf import settings
            shop_id = getattr(settings, 'YOOKASSA_SHOP_ID', None)
            secret_key = getattr(settings, 'YOOKASSA_SECRET_KEY', None)
            
            if not shop_id or not secret_key:
                logger.error(f"Настройки ЮKassa не найдены: shop_id={shop_id}, secret_key={'***' if secret_key else None}")
                return None
            
            logger.info(f"Создаем платеж ЮKassa: shop_id={shop_id}, amount={amount}")
            
            # Создаем платеж
            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url
                },
                "capture": False,  # Двухэтапное подтверждение
                "description": description,
                "metadata": {
                    "payment_id": str(payment_id),  # ✅ Добавляем payment_id
                    "user_id": str(user_id),
                    "subscription_type": "balance_deposit"  # ✅ Используем subscription_type
                }
            }
            
            url = "https://api.yookassa.ru/v3/payments"
            
            # Правильно кодируем secret key для Basic Auth
            import base64
            auth_string = f"{shop_id}:{secret_key}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json",
                "Idempotence-Key": str(uuid.uuid4())
            }
            
            response = requests.post(url, json=payment_data, headers=headers, timeout=30)
            
            logger.info(f"Ответ ЮKassa: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'payment_id': data['id'],
                    'confirmation_url': data['confirmation']['confirmation_url']
                }
            else:
                logger.error(f"Ошибка создания платежа ЮKassa: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка создания платежа ЮKassa: {e}")
            return None


class BroadcastService:
    def __init__(self):
        self.bot_token = BOT_TOKEN

    def send_broadcast(self, broadcast: Broadcast) -> bool:
        """Отправляет рассылку"""
        try:
            self._send_broadcast_sync(broadcast)
            return True
        except Exception as e:
            logger.error(f"Ошибка запуска рассылки {broadcast.broadcast_id}: {e}")
            return False

    def send_broadcast_with_options(self, broadcast: Broadcast, image_file=None, 
                                  send_to_admins=False, disable_web_page_preview=False, 
                                  disable_notification=False) -> bool:
        """Отправляет рассылку с дополнительными опциями"""
        try:
            self._send_broadcast_with_options_sync(
                broadcast, image_file, send_to_admins, disable_web_page_preview, disable_notification
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка запуска рассылки с опциями {broadcast.broadcast_id}: {e}")
            return False

    def _send_broadcast_sync(self, broadcast: Broadcast):
        """Синхронная отправка рассылки"""
        try:
            import requests
            
            users = TelegramUser.objects.all()
            broadcast.total_count = users.count()
            broadcast.status = 'pending'
            broadcast.save()

            sent_count = 0
            failed_count = 0

            for user in users:
                try:
                    self._send_telegram_message_sync(
                        user.user_id, 
                        broadcast.message_text, 
                        parse_mode="HTML",
                        image_path=None
                    )
                    sent_count += 1
                    
                    # Обновляем счетчик каждые 10 сообщений
                    if sent_count % 10 == 0:
                        broadcast.sent_count = sent_count
                        broadcast.save()
                        
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Ошибка отправки пользователю {user.user_id}: {e}")

            # Финальное обновление
            broadcast.sent_count = sent_count
            broadcast.status = 'sent'
            broadcast.save()

            logger.info(f"Рассылка {broadcast.broadcast_id} завершена: {sent_count} отправлено, {failed_count} ошибок")

        except Exception as e:
            logger.error(f"Ошибка рассылки {broadcast.broadcast_id}: {e}")
            broadcast.status = 'failed'
            broadcast.save()

    def _send_telegram_message_sync(self, chat_id: int, text: str, parse_mode: str = None, image_path: str = None):
        """Синхронная отправка сообщения в Telegram с возможностью добавления изображения"""
        import requests
        from pathlib import Path
        
        if image_path and Path(image_path).exists():
            # Отправляем фото с подписью (оптимизированно)
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
            
            with open(image_path, 'rb') as photo:
                files = {'photo': photo}
                data = {
                    'chat_id': chat_id,
                    'caption': text,
                    'disable_notification': False
                }
                
                if parse_mode:
                    data['parse_mode'] = parse_mode
                    
                # Уменьшаем timeout для быстрой отправки
                response = requests.post(url, files=files, data=data, timeout=5)
        else:
            # Отправляем обычное текстовое сообщение
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            data = {
                'chat_id': chat_id,
                'text': text,
                'disable_notification': False
            }
            
            if parse_mode:
                data['parse_mode'] = parse_mode
                
            response = requests.post(url, data=data, timeout=5)
        
        if response.status_code != 200:
            raise Exception(f"Telegram API error: {response.status_code} - {response.text}")
            
        return response.json()

    def _send_broadcast_with_options_sync(self, broadcast: Broadcast, image_file=None,
                                         send_to_admins=False, disable_web_page_preview=False,
                                         disable_notification=False):
        """Синхронная отправка рассылки с опциями"""
        try:
            users = TelegramUser.objects.all()
            broadcast.total_count = users.count()
            broadcast.status = 'pending'
            broadcast.save()

            sent_count = 0
            failed_count = 0

            # Параметры отправки
            send_kwargs = {
                'parse_mode': 'HTML',
                'disable_web_page_preview': disable_web_page_preview,
                'disable_notification': disable_notification
            }

            for user in users:
                try:
                    # Отправляем только текст без изображений
                    self._send_telegram_message_sync(
                        user.user_id, 
                        broadcast.message_text, 
                        parse_mode="HTML",
                        image_path=None
                    )
                    
                    sent_count += 1
                    
                    # Обновляем счетчик каждые 10 сообщений
                    if sent_count % 10 == 0:
                        broadcast.sent_count = sent_count
                        broadcast.save()
                        
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Ошибка отправки пользователю {user.user_id}: {e}")

            # Отправляем копию администраторам если нужно
            if send_to_admins:
                from config import ADMIN_IDS
                for admin_id in ADMIN_IDS:
                    try:
                        self._send_telegram_message_sync(
                            admin_id,
                            f"📢 Копия рассылки:\n\n{broadcast.message_text}",
                            parse_mode="HTML",
                            image_path=None
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки копии админу {admin_id}: {e}")

            # Финальное обновление
            broadcast.sent_count = sent_count
            broadcast.status = 'sent'
            broadcast.save()

            logger.info(f"Рассылка {broadcast.broadcast_id} завершена: {sent_count} отправлено, {failed_count} ошибок")

        except Exception as e:
            logger.error(f"Ошибка рассылки {broadcast.broadcast_id}: {e}")
            broadcast.status = 'failed'
            broadcast.save()


class SupportService:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)

    def send_message_to_user_sync(self, chat_id: int, message_text: str) -> bool:
        """Синхронная отправка сообщения пользователю от поддержки"""
        try:
            import requests
            import json
            from .models import SupportChat
            
            # Получаем chat_id пользователя
            chat = SupportChat.objects.get(chat_id=chat_id)
            user_id = chat.user.user_id
            
            # Отправляем сообщение через Telegram Bot API напрямую
            bot_token = BOT_TOKEN
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            # Создаем клавиатуру с кнопкой
            keyboard = {
                "inline_keyboard": [
                    [{"text": "💬 Ответить поддержке", "callback_data": "reply_to_support"}]
                ]
            }
            
            data = {
                'chat_id': user_id,
                'text': f"💬 <b>Ответ от поддержки:</b>\n\n{message_text}",
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            }
            
            # Отправляем обычное сообщение с кнопкой
            response = requests.post(url, data=data)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('ok'):
                    # Сохраняем сообщение в базу данных
                    from .models import SupportMessage
                    SupportMessage.objects.create(
                        chat=chat,
                        sender='admin',
                        text=message_text,
                        is_read=False
                    )
                    
                    # Обновляем счетчик непрочитанных сообщений от админа
                    chat.unread_admin_messages += 1
                    chat.save()
                    
                    logger.info(f"Сообщение отправлено пользователю {user_id} и сохранено в БД")
                    return True
                else:
                    logger.error(f"Ошибка Telegram API: {result.get('description')}")
                    return False
            else:
                logger.error(f"HTTP ошибка {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка синхронной отправки сообщения поддержки в чат {chat_id}: {e}")
            return False

    async def send_message_to_user(self, chat_id: int, message_text: str) -> bool:
        """Отправляет сообщение пользователю от поддержки"""
        try:
            from .models import SupportChat, SupportMessage
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            # Создаем клавиатуру с кнопкой
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Ответить поддержке", callback_data="reply_to_support")]
            ])
            
            # Отправляем пользователю с кнопкой
            await self.bot.send_message(
                chat.user.user_id, 
                f"💬 <b>Ответ от поддержки:</b>\n\n{message_text}",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return True
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения поддержки в чат {chat_id}: {e}")
            return False

    async def notify_admins_new_message(self, chat_id: int, user_id: int, message_text: str):
        """Уведомляет админов о новом сообщении в поддержке"""
        try:
            from .models import TelegramUser
            from asgiref.sync import sync_to_async
            
            user = await sync_to_async(TelegramUser.objects.get)(user_id=user_id)
            username = user.username or ""
            user_display = f"@{username}" if username else f"ID{user.user_id}"
            
            message = f"📬 Новое сообщение от {user_display}:\n{message_text}"
            
            for admin_id in ADMIN_IDS:
                try:
                    await self.bot.send_message(admin_id, message)
                except Exception as e:
                    logger.error(f"Ошибка уведомления админа {admin_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка уведомления админов о новом сообщении: {e}")

    def update_unread_counters(self, chat_id: int, sender: str):
        """Обновляет счетчики непрочитанных сообщений"""
        try:
            from .models import SupportChat
            
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            if sender == 'user':
                # Сообщение от пользователя - увеличиваем счетчик для админа
                chat.unread_admin_messages += 1
            elif sender == 'admin':
                # Сообщение от админа - увеличиваем счетчик для пользователя
                chat.unread_user_messages += 1
                
            chat.save()
            
        except Exception as e:
            logger.error(f"Ошибка обновления счетчиков непрочитанных сообщений: {e}")

    def mark_messages_as_read(self, chat_id: int, reader: str):
        """Отмечает сообщения как прочитанные"""
        try:
            from .models import SupportChat, SupportMessage
            
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            if reader == 'admin':
                # Админ прочитал - обнуляем счетчик непрочитанных сообщений от пользователя
                chat.unread_admin_messages = 0
                # Отмечаем все сообщения от пользователя как прочитанные
                SupportMessage.objects.filter(
                    chat=chat, 
                    sender='user', 
                    is_read=False
                ).update(is_read=True)
            elif reader == 'user':
                # Пользователь прочитал - обнуляем счетчик непрочитанных сообщений от админа
                chat.unread_user_messages = 0
                # Отмечаем все сообщения от админа как прочитанные
                SupportMessage.objects.filter(
                    chat=chat, 
                    sender='admin', 
                    is_read=False
                ).update(is_read=True)
                
            chat.save()
            
        except Exception as e:
            logger.error(f"Ошибка отметки сообщений как прочитанных: {e}")


class SubscriptionReminderService:
    """Сервис для отправки напоминаний о подписках"""
    
    def __init__(self):
        self.bot_token = BOT_TOKEN
    
    def send_subscription_reminder(self, payment: Payment, reminder_type: str) -> bool:
        """Отправляет напоминание о подписке пользователю"""
        try:
            from .models import SubscriptionReminder
            import requests
            import json
            
            # Проверяем, не отправляли ли уже это напоминание
            reminder_exists = SubscriptionReminder.objects.filter(
                payment=payment,
                reminder_type=reminder_type
            ).exists()
            
            if reminder_exists:
                logger.info(f"Напоминание {reminder_type} для платежа {payment.payment_id} уже отправлено")
                return False
            
            # Формируем текст сообщения
            if reminder_type == '2_days_before':
                message_text = self._get_2_days_before_message(payment)
            elif reminder_type == 'expired':
                message_text = self._get_expired_message(payment)
            else:
                logger.error(f"Неизвестный тип напоминания: {reminder_type}")
                return False
            
            # Создаем клавиатуру с кнопками
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Продлить подписку", "callback_data": f"renew_subscription:{payment.payment_id}"}],
                    [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
                ]
            }
            
            # Отправляем сообщение через Telegram Bot API
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': payment.user.user_id,
                'text': message_text,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            }
            
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('ok'):
                    # Сохраняем запись о напоминании
                    SubscriptionReminder.objects.create(
                        payment=payment,
                        reminder_type=reminder_type
                    )
                    logger.info(f"Напоминание {reminder_type} отправлено пользователю {payment.user.user_id} для платежа {payment.payment_id}")
                    return True
                else:
                    logger.error(f"Ошибка Telegram API: {result.get('description')}")
                    return False
            else:
                logger.error(f"HTTP ошибка {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания {reminder_type} для платежа {payment.payment_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _get_2_days_before_message(self, payment: Payment) -> str:
        """Формирует текст сообщения за 2 дня до окончания подписки"""
        from datetime import datetime
        
        expires_at = payment.subscription_expires_at
        if expires_at:
            expires_date = expires_at.strftime("%d.%m.%Y")
        else:
            expires_date = "скоро"
        
        sub_type_text = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }.get(payment.subscription_type, 'Подписка')

        return f"""
⏰ <b>Напоминание о подписке</b>

📅 <b>Ваша {sub_type_text.lower()} заканчивается через 2 дня!</b>

📆 <b>Дата окончания:</b> {expires_date}

💡 <b>Не забудьте продлить подписку</b>, чтобы не потерять доступ к сервису.

🔄 <b>Продлите подписку сейчас</b> и продолжайте пользоваться без перерывов!
"""

    def _get_expired_message(self, payment: Payment) -> str:
        """Формирует текст сообщения об истекшей подписке"""
        sub_type_text = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }.get(payment.subscription_type, 'Подписка')
        
        return f"""
❌ <b>Подписка истекла</b>

📅 <b>Ваша {sub_type_text.lower()} закончилась.</b>

🔒 <b>Доступ к сервису временно ограничен.</b>

🔄 <b>Продлите подписку</b>, чтобы восстановить доступ и продолжить пользоваться сервисом без ограничений!
"""


# Импорты для использования в сервисах
from django.utils import timezone
from django.db import models
