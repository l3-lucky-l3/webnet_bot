"""
Сервис для обработки платежей Обычный VPN (бывший Обычный VPN, Remnawave)
Автоматическая генерация ключей через Remnawave API
"""

import time
import logging
from typing import Optional, Dict, Any
from django.utils import timezone
from datetime import datetime, timedelta
import requests
import json

from bot_management.models import Payment, SubscriptionKey, TelegramUser
from bot_management.remnawave_api import get_remnawave_client, RemnawaveAPIError
from config import BOT_TOKEN

logger = logging.getLogger(__name__)


def _send_payment_success_notification_sync(payment: Payment, key: str):
    """
    Синхронная отправка уведомления об успешной оплате для Обычного VPN
    Отправляет 2 сообщения: инструкция с картинкой (без текста про глушилки), затем подтверждение с кнопками
    """
    try:
        # Вычисляем дату окончания подписки
        expires_at = payment.subscription_expires_at
        expires_date = expires_at.strftime('%d.%m.%Y') if expires_at else '—'
        
        # Сообщение 1: Инструкция с картинкой (без текста про глушилки)
        instruction_message = f"""📲<b>Установка и настройка</b>

Мы рекомендуем это приложение👇
<a href="https://incy.cc/">INCY</a> : https://incy.cc/

🙏<b>УСТАНОВКА</b>
1.Скачиваем приложение <a href="https://incy.cc/">INCY</a> ( есть в AppStore и PlayMarket)
2. Нажимаем ( +Добавить )
3. Вставляем ссылку ключа
 
ГОТОВО✅

⚠️<b>Условия использования</b>

· Доступ на 3 устройства
· При нарушении правил — бан без возврата средств

🔒<b>Безопасность:</b>

· Не передавайте свой личный ключ третьим лицам
· При нарушении этого правила доступ может быть <b>заблокирован без возможности возврата средств</b>

⚙️<b>Решение небольших проблем</b>:

· Обновить конфигурацию ( кнопка правее названия "WebNet" )
· Запустить проверку пинга ( кнопка молнии, рядом с обновлением )
· Перезапустить приложение
· Включить/выключить VPN"""

        # Сообщение 2: Подтверждение оплаты с кнопками
        confirmation_message = f"""✅ Оплата подтверждена!

🚀 Обычный VPN - Ключ активирован

🔑 Ваш ключ доступа:
{key}

📅 Действует до: {expires_date}"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "🚀 Открыть ключ", "url": key}],
                [
                    {"text": "⬅️ Главное меню", "callback_data": "main_menu"},
                    {"text": "💬 Написать менеджеру", "url": "https://t.me/yamalube61"}
                ]
            ]
        }

        from pathlib import Path
        import time
        
        # Отправляем первое сообщение с картинкой
        image_path = "images/instruction.jpg"
        if Path(image_path).exists():
            with open(image_path, 'rb') as photo:
                files = {'photo': photo}
                data = {
                    'chat_id': payment.user.user_id,
                    'caption': instruction_message,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': 'true'
                }
                response = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    files=files,
                    data=data,
                    timeout=10
                )
                if response.status_code != 200:
                    logger.error(f"Telegram API вернул ошибку {response.status_code}: {response.text}")
        else:
            # Если картинки нет, отправляем текстом
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(
                url,
                data={
                    'chat_id': payment.user.user_id,
                    'text': instruction_message,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': 'true'
                },
                timeout=5
            )

        # Задержка перед отправкой второго сообщения
        time.sleep(1.5)

        # Отправляем второе сообщение с подтверждением и кнопками
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        response = requests.post(
            url,
            data={
                'chat_id': payment.user.user_id,
                'text': confirmation_message,
                'reply_markup': json.dumps(keyboard),
                'disable_web_page_preview': 'true'
            },
            timeout=10
        )

        if response.status_code != 200:
            logger.error(f"Telegram API вернул ошибку {response.status_code}: {response.text}")
            raise Exception(f"Telegram API error: {response.status_code} - {response.text}")

        result = response.json()
        if not result.get('ok'):
            error_desc = result.get('description', 'Unknown error')
            logger.error(f"Telegram API вернул ok=false: {error_desc}")
            raise Exception(f"Telegram API error: {error_desc}")

        logger.info(f"Уведомление успешно отправлено пользователю {payment.user.user_id}, message_id={result.get('result', {}).get('message_id')}")

    except requests.exceptions.Timeout:
        logger.error(f"Таймаут при отправке уведомления пользователю {payment.user.user_id}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при отправке уведомления пользователю {payment.user.user_id}: {e}")
        raise
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {payment.user.user_id}: {e}")
        raise


# Маппинг типов подписок на длительность в днях
SUBSCRIPTION_DURATION_MAP = {
    'trial': 3,  # Пробный период - 3 дня
    'regular_trial': 3,  # Пробный период для Regular VPN - 3 дня
    'fast_trial': 3,  # Пробный период для Fast VPN - 3 дня
    'day': 1,
    'month': 30,
    '3months': 90,
    '6months': 180,
    'year': 365,
    '2years': 730
}


async def create_regular_vpn_payment(
    user_id: int,
    subscription_type: str,
    amount: float
) -> Optional[Dict[str, Any]]:
    """
    Создать платеж для Обычный VPN
    
    Args:
        user_id: Telegram ID пользователя
        subscription_type: Тип подписки (day, month, 3months, 6months, year, 2years)
        amount: Сумма платежа
        
    Returns:
        Dict с данными платежа или None
    """
    try:
        # Получаем или создаем пользователя Django
        user, created = TelegramUser.objects.get_or_create(
            user_id=user_id,
            defaults={'username': f'user_{user_id}'}
        )
        
        # Создаем платеж
        payment = Payment.objects.create(
            user=user,
            vpn_type='regular',
            subscription_type=f'regular_{subscription_type}',
            amount=int(amount),
            status='pending'
        )
        
        logger.info(f"Создан платеж {payment.payment_id} для Обычный VPN, пользователь {user_id}")
        
        return {
            'success': True,
            'payment_id': payment.payment_id,
            'subscription_type': f'regular_{subscription_type}',
            'amount': amount,
            'vpn_type': 'regular'
        }
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа для Обычного VPN: {e}")
        return None


def process_regular_vpn_payment_success_sync(
    payment_id: int,
    skip_notification: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Обработать успешный платеж для Обычный VPN (СИНХРОННАЯ ВЕРСИЯ)
    Генерирует ключ через Remnawave API

    Args:
        payment_id: ID платежа в базе данных

    Returns:
        Dict с данными ключа или None
    """
    import asyncio
    
    try:
        # Получаем платеж
        payment = Payment.objects.get(payment_id=payment_id)

        if payment.status == 'succeeded' and payment.issued_key:
            logger.warning(f"Платеж {payment_id} уже обработан с ключом")
            return {
                'success': False,
                'error': 'Платеж уже обработан'
            }

        # Обновляем статус платежа
        payment.status = 'succeeded'
        payment.paid_at = timezone.now()

        # Вычисляем дату окончания подписки
        subscription_type = payment.subscription_type.replace('regular_', '')
        duration_days = SUBSCRIPTION_DURATION_MAP.get(subscription_type, 30)
        payment.subscription_expires_at = timezone.now() + timedelta(days=duration_days)

        user_id = payment.user.user_id

        # Генерируем ключ через Remnawave API
        remnawave_client = get_remnawave_client()

        if not remnawave_client:
            logger.error("Remnawave API клиент не инициализирован")
            payment.save()
            return {
                'success': False,
                'error': 'Remnawave API недоступен'
            }

        try:
            # Получаем или создаем пользователя в Remnawave
            logger.info(f"Платеж {payment_id}: Получаем пользователя Remnawave для telegram_id={user_id}")

            # Генерируем уникальное имя пользователя с номером платежа и timestamp
            import time
            remnawave_username = f"{payment.user.username or f'user_{user_id}'}_{payment_id}_{int(time.time())}"

            logger.info(f"Платеж {payment_id}: Создаем Remnawave пользователя с username={remnawave_username}")

            # Запускаем async функцию в синхронном контексте
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # ВСЕГДА создаем НОВОГО пользователя для каждого платежа (не ищем существующего)
                remnawave_user = loop.run_until_complete(
                    remnawave_client.create_user(
                        telegram_id=user_id,
                        username=remnawave_username,
                        expire_days=duration_days
                    )
                )
            finally:
                loop.close()

            logger.info(f"Платеж {payment_id}: Remnawave пользователь создан: uuid={remnawave_user.get('uuid')}, subscriptionUrl={remnawave_user.get('subscriptionUrl')}")

            remnawave_user_uuid = remnawave_user.get('uuid')
            remnawave_user_id = remnawave_user.get('id')

            if not remnawave_user_uuid:
                logger.error(f"Платеж {payment_id}: Не удалось получить UUID пользователя в Remnawave")
                raise RemnawaveAPIError("Не удалось получить UUID пользователя в Remnawave")

            logger.info(f"Платеж {payment_id}: Продлеваем подписку для subscription_type={subscription_type}")

            # Продлеваем подписку пользователя (（обновляем expireAt) - напрямую по UUID
            from datetime import timezone as dt_timezone
            expire_dt = datetime.now(dt_timezone.utc) + timedelta(days=duration_days)
            new_expire_at = expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{expire_dt.microsecond // 1000:03d}Z"

            # Устанавливаем лимит трафика (120 ГБ для всех тарифов, 10 ГБ для trial):
            # - 10ГБ для trial
            # - 120ГБ для day (1 день)
            # - 120ГБ для month
            # - 120ГБ для 3months
            # - 120ГБ для 6months
            # - 120ГБ для year
            # - 120ГБ для 2years
            traffic_limit_bytes = None
            traffic_map = {
                'trial': 10,
                'regular_trial': 10,
                'fast_trial': 10,
                'day': 120,
                'month': 120,
                '3months': 120,
                '6months': 120,
                'year': 120,
                '2years': 120,
            }
            traffic_gb = traffic_map.get(subscription_type)
            if traffic_gb is not None:
                traffic_limit_bytes = traffic_gb * 1024 * 1024 * 1024
                logger.info(f"Платеж {payment_id}: Установлен лимит трафика {traffic_gb}ГБ для {subscription_type}")

            update_data = {
                'uuid': remnawave_user_uuid,
                'expireAt': new_expire_at,
                'status': 'ACTIVE',
                'hwidDeviceLimit': 3,  # Ограничение на 3 устройства
            }

            # Добавляем лимит трафика если указан
            if traffic_limit_bytes is not None:
                update_data['trafficLimitBytes'] = traffic_limit_bytes

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                updated_user = loop.run_until_complete(
                    remnawave_client._request('PATCH', '/api/users', update_data)
                )
            finally:
                loop.close()
            
            updated_user = updated_user.get('response', {})

            # Извлекаем subscription URL
            subscription_key = updated_user.get('subscriptionUrl') or remnawave_user.get('subscriptionUrl')

            logger.info(f"Платеж {payment_id}: Извлеченный ключ: {subscription_key[:50] if subscription_key else 'None'}...")

            if not subscription_key:
                logger.error(f"Платеж {payment_id}: Не удалось извлечь subscription URL. Ответ API: {updated_user}")
                raise RemnawaveAPIError(f"Не удалось извлечь subscription URL: {updated_user}")

            remnawave_key_id = updated_user.get('id') or remnawave_user_id
            remnawave_user_uuid = updated_user.get('uuid') or remnawave_user.get('uuid')

            logger.info(f"Платеж {payment_id}: Сохраняем ключ в БД, remnawave_key_id={remnawave_key_id}, remnawave_user_uuid={remnawave_user_uuid}")

            # Проверяем, существует ли уже такой ключ
            existing_key = SubscriptionKey.objects.filter(key_value=subscription_key).first()
            if existing_key:
                logger.info(f"Платеж {payment_id}: Ключ уже существует в БД, используем его")
                subscription_key_obj = existing_key
            else:
                # Сохраняем новый ключ в базу данных
                subscription_key_obj = SubscriptionKey.objects.create(
                    key_value=subscription_key,
                    vpn_type='regular',
                    subscription_type=payment.subscription_type,
                    total_activations=1,
                    used_activations=0,
                    is_active=True,
                    remnawave_user_id=remnawave_user_id,
                    remnawave_user_uuid=remnawave_user_uuid,
                    remnawave_key_id=remnawave_key_id
                )

            # Обновляем платеж
            payment.issued_key = subscription_key
            payment.subscription_expires_at = timezone.now() + timedelta(days=duration_days)
            payment.save()

            logger.info(f"Платеж {payment_id}: Успешная оплата Обычный VPN. Ключ {subscription_key_obj.key_id}")

            # Отправляем уведомление пользователю (только если не skip_notification)
            if not skip_notification:
                try:
                    _send_payment_success_notification_sync(payment, subscription_key)
                    logger.info(f"Платеж {payment_id}: Уведомление отправлено пользователю {payment.user.user_id}")
                except Exception as e:
                    logger.error(f"Платеж {payment_id}: Ошибка отправки уведомления: {e}")
                    import traceback
                    logger.error(traceback.format_exc())

            # Обрабатываем реферальную систему
            try:
                from .referral_services import ReferralService
                referral_service = ReferralService()
                result = referral_service.process_referral_purchase_sync(payment.user.user_id, payment)
                logger.info(f"Платеж {payment_id}: Результат обработки реферала: {result}")
            except Exception as e:
                logger.error(f"Платеж {payment_id}: Ошибка обработки реферала: {e}")
                import traceback
                logger.error(traceback.format_exc())

            return {
                'success': True,
                'key': subscription_key,
                'key_id': subscription_key_obj.key_id,
                'expires_at': payment.subscription_expires_at,
                'duration_days': duration_days
            }

        except RemnawaveAPIError as e:
            logger.error(f"Платеж {payment_id}: Ошибка Remnawave API при генерации ключа: {e}")
            payment.save()
            return {
                'success': False,
                'error': f'Ошибка генерации ключа: {str(e)}'
            }

    except Payment.DoesNotExist:
        logger.error(f"Платеж {payment_id} не найден")
        return None
    except Exception as e:
        logger.error(f"Ошибка обработки платежа {payment_id}: {e}")
        return None


def process_regular_vpn_payment_success_sync_with_retry(
    payment_id: int,
    skip_notification: bool = False,
    max_retries: int = 3,
    retry_delay: float = 2.0
) -> Optional[Dict[str, Any]]:
    """
    Обработка платежа Regular VPN с retry логикой.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Regular VPN платеж {payment_id}: попытка {attempt}/{max_retries}")
            result = process_regular_vpn_payment_success_sync(payment_id, skip_notification=skip_notification)
            
            if result and result.get('success'):
                if attempt > 1:
                    logger.info(f"Regular VPN платеж {payment_id}: успешно со {attempt}-й попытки")
                return result
            
            error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Пустой ответ'
            last_error = error_msg
            logger.warning(f"Regular VPN платеж {payment_id}: попытка {attempt} неудачна: {error_msg}")
            
            if attempt < max_retries:
                logger.info(f"Regular VPN платеж {payment_id}: ждём {retry_delay}с перед следующей попыткой")
                time.sleep(retry_delay)
                
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Regular VPN платеж {payment_id}: попытка {attempt} исключение: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
    
    logger.error(f"Regular VPN платеж {payment_id}: все {max_retries} попыток неудачны. Последняя ошибка: {last_error}")
    return {'success': False, 'error': f'API недоступен после {max_retries} попыток: {last_error}'}


async def get_user_regular_vpn_keys(user_id: int) -> list:
    """
    Получить все ключи Обычный VPN пользователя

    Args:
        user_id: Telegram ID пользователя

    Returns:
        List с ключами
    """
    try:
        keys = SubscriptionKey.objects.filter(
            user_id=user_id,
            vpn_type='regular',
            is_active=True
        ).order_by('-key_id')

        result = []
        for key in keys:
            result.append({
                'key_id': key.key_id,
                'key_value': key.key_value,
                'subscription_type': key.get_subscription_type_display(),
                'is_active': key.is_active,
                'remaining_activations': key.remaining_activations
            })

        return result

    except Exception as e:
        logger.error(f"Ошибка получения ключей пользователя {user_id}: {e}")
        return []


async def extend_regular_vpn_subscription(
    user_id: int,
    subscription_type: str
) -> Optional[Dict[str, Any]]:
    """
    Продлить подписку Обычный VPN
    
    Args:
        user_id: Telegram ID пользователя
        subscription_type: Тип подписки для продления
        
    Returns:
        Dict с результатами продления
    """
    try:
        remnawave_client = get_remnawave_client()
        
        if not remnawave_client:
            return {
                'success': False,
                'error': 'Remnawave API недоступен'
            }
        
        duration_days = SUBSCRIPTION_DURATION_MAP.get(subscription_type, 30)
        
        # Продлеваем подписку
        result = await remnawave_client.extend_subscription(
            telegram_id=user_id,
            duration_days=duration_days
        )
        
        logger.info(f"Подписка пользователя {user_id} продлена на {duration_days} дней")
        
        return {
            'success': True,
            'new_expires_at': result.get('expires_at'),
            'duration_days': duration_days
        }
        
    except Exception as e:
        logger.error(f"Ошибка продления подписки: {e}")
        return {
            'success': False,
            'error': str(e)
        }
