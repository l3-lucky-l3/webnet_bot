"""
Сервис для работы с Night VPN (Обход) через Remnawave Bypass API.

Заменяет старую логику с FGN Connection API и пулом ключей.
Использует отдельный инстанс Remnawave с другим токеном для обхода.
"""

import time
import logging
import asyncio
from datetime import timedelta, datetime
from typing import Optional

from django.utils import timezone
from django.db import transaction

from .remnawave_api import RemnawaveAPI, RemnawaveAPIError, get_remnawave_bypass_client
from .models import Payment, TelegramUser

logger = logging.getLogger(__name__)


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


SUBSCRIPTION_DURATION_MAP = {
    'trial': 3,
    'week': 7,
    'month': 30,
    '3months': 90,
    '6months': 180,
    'year': 365,
}

TRAFFIC_LIMIT_MAP = {
    'trial': 10,
    'week': 120,
    'month': 120,
    '3months': 120,
    '6months': 120,
    'year': 120,
}


def _calculate_expiry_date(subscription_type: str, paid_at) -> timezone.datetime:
    duration_days = SUBSCRIPTION_DURATION_MAP.get(subscription_type, 30)
    return paid_at + timedelta(days=duration_days)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def process_night_vpn_payment_sync_with_retry(payment_id: int, max_retries: int = 3, retry_delay: float = 2.0) -> dict:
    """
    Обработка платежа Night VPN с retry логикой.
    Пробует до max_retries раз с задержкой retry_delay между попытками.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Night VPN платеж {payment_id}: попытка {attempt}/{max_retries}")
            result = process_night_vpn_payment_sync(payment_id)
            
            if result and result.get('success'):
                if attempt > 1:
                    logger.info(f"Night VPN платеж {payment_id}: успешно со {attempt}-й попытки")
                return result
            
            error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Пустой ответ'
            last_error = error_msg
            logger.warning(f"Night VPN платеж {payment_id}: попытка {attempt} неудачна: {error_msg}")
            
            if attempt < max_retries:
                logger.info(f"Night VPN платеж {payment_id}: ждём {retry_delay}с перед следующей попыткой")
                time.sleep(retry_delay)
                
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Night VPN платеж {payment_id}: попытка {attempt} исключение: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
    
    logger.error(f"Night VPN платеж {payment_id}: все {max_retries} попыток неудачны. Последняя ошибка: {last_error}")
    return {'success': False, 'error': f'API недоступен после {max_retries} попыток: {last_error}'}


def process_night_vpn_payment_sync(payment_id: int) -> dict:
    """
    Обработка платежа Night VPN через Remnawave Bypass API.

    Создает пользователя в bypass Remnawave, получает subscriptionUrl как ключ.
    """
    try:
        payment = Payment.objects.select_related('user').get(payment_id=payment_id)
        logger.info(f"Обработка платежа Night VPN {payment_id} через Remnawave Bypass API, подписка: {payment.subscription_type}")

        if payment.vpn_type != 'night':
            return {'success': False, 'error': 'Не Night VPN платеж'}

        bypass_client = get_remnawave_bypass_client()
        if not bypass_client:
            return {'success': False, 'error': 'Remnawave Bypass API не настроен'}

        subscription_type = payment.subscription_type
        duration_days = SUBSCRIPTION_DURATION_MAP.get(subscription_type, 30)
        user_id = payment.user.user_id

        import uuid as uuid_mod
        clean_name = ''.join(c for c in (payment.user.username or f'user{user_id}') if c.isalnum() or c in '_-')
        clean_name = clean_name[:20]
        if len(clean_name) < 3:
            clean_name = f"usr{clean_name}"
        remnawave_username = f"bp_{clean_name}_{uuid_mod.uuid4().hex[:6]}"

        remnawave_user = _run_async(
            bypass_client.create_user(
                telegram_id=user_id,
                username=remnawave_username,
                expire_days=duration_days
            )
        )

        remnawave_user_uuid = remnawave_user.get('uuid')
        if not remnawave_user_uuid:
            raise RemnawaveAPIError("Не удалось получить UUID пользователя в Remnawave Bypass")

        from datetime import timezone as dt_timezone
        expire_dt = datetime.now(dt_timezone.utc) + timedelta(days=duration_days)
        new_expire_at = expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{expire_dt.microsecond // 1000:03d}Z"

        traffic_gb = TRAFFIC_LIMIT_MAP.get(subscription_type)
        traffic_limit_bytes = traffic_gb * 1024 * 1024 * 1024 if traffic_gb else None

        update_data = {
            'uuid': remnawave_user_uuid,
            'expireAt': new_expire_at,
            'status': 'ACTIVE',
        }
        if traffic_limit_bytes is not None:
            update_data['trafficLimitBytes'] = traffic_limit_bytes

        updated_user = _run_async(bypass_client._request('PATCH', '/api/users', update_data))
        updated_user = updated_user.get('response', {})

        subscription_key = updated_user.get('subscriptionUrl') or remnawave_user.get('subscriptionUrl')

        if not subscription_key:
            raise RemnawaveAPIError(f"Не удалось извлечь subscription URL: {updated_user}")

        with transaction.atomic():
            payment.issued_key = subscription_key
            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.subscription_expires_at = _calculate_expiry_date(subscription_type, payment.paid_at)
            payment.current_key_expires_at = payment.subscription_expires_at
            payment.fgcn_key_id = str(updated_user.get('id') or remnawave_user.get('id') or '')
            payment.fgcn_tg_id = user_id
            payment.is_fgn_key = True
            payment.bypass_remnawave_uuid = remnawave_user_uuid
            payment.save()

            if subscription_type == 'trial':
                try:
                    user = TelegramUser.objects.get(user_id=user_id)
                    user.trial_key_used_night = True
                    user.save()
                except TelegramUser.DoesNotExist:
                    pass

        logger.info(f"Платеж {payment_id} успешно обработан, ключ: {subscription_key[:30]}...")

        try:
            from .referral_services import ReferralService
            from config import BOT_TOKEN
            from aiogram import Bot

            bot = Bot(token=BOT_TOKEN)
            referral_service = ReferralService(bot=bot)
            referral_service.process_referral_purchase_sync(user_id, payment)
        except Exception as e:
            logger.error(f"Ошибка обработки реферала: {e}")

        return {
            'success': True,
            'key_value': subscription_key,
            'fgcn_key_id': payment.fgcn_key_id,
        }

    except RemnawaveAPIError as e:
        logger.error(f"Ошибка Remnawave Bypass API при обработке платежа {payment_id}: {e}")
        return {'success': False, 'error': f'Ошибка API: {str(e)}'}
    except Payment.DoesNotExist:
        logger.error(f"Платеж {payment_id} не найден")
        return {'success': False, 'error': 'Платеж не найден'}
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке платежа {payment_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {'success': False, 'error': str(e)}


def renew_night_vpn_key_sync(payment_id: int) -> bool:
    """
    Продление ключа Night VPN через Remnawave Bypass API.
    Обновляет expireAt у пользователя в bypass Remnawave.
    """
    try:
        payment = Payment.objects.select_related('user').get(payment_id=payment_id)

        if not payment.is_fgn_key or not payment.bypass_remnawave_uuid:
            logger.warning(f"Платеж {payment_id} не имеет bypass Remnawave UUID для продления")
            return False

        bypass_client = get_remnawave_bypass_client()
        if not bypass_client:
            logger.error("Remnawave Bypass API клиент не инициализирован")
            return False

        logger.info(f"Bypass ключ {payment.bypass_remnawave_uuid} для платежа {payment_id} активен до {payment.subscription_expires_at}")
        return True

    except Exception as e:
        logger.error(f"Ошибка проверки bypass ключа для платежа {payment_id}: {e}")
        return False


def extend_bypass_subscription(payment: Payment, duration_days: int) -> bool:
    """
    Продлевает подписку Night VPN в bypass Remnawave.

    Args:
        payment: Оригинальный платеж с bypass_remnawave_uuid
        duration_days: На сколько дней продлить

    Returns:
        True если успешно
    """
    try:
        bypass_uuid = payment.bypass_remnawave_uuid
        if not bypass_uuid:
            logger.error(f"Нет bypass_remnawave_uuid у платежа {payment.payment_id}")
            return False

        bypass_client = get_remnawave_bypass_client()
        if not bypass_client:
            logger.error("Remnawave Bypass API клиент не инициализирован")
            return False

        user = _run_async(bypass_client.get_user_by_uuid(bypass_uuid))
        if not user:
            logger.error(f"Пользователь Bypass Remnawave с UUID {bypass_uuid} не найден")
            return False

        current_expire = user.get('expireAt')
        if current_expire:
            try:
                current_expire_dt = datetime.fromisoformat(current_expire.replace('Z', '+00:00'))
                new_expire_dt = current_expire_dt + timedelta(days=duration_days)
            except Exception:
                new_expire_dt = datetime.utcnow() + timedelta(days=duration_days)
        else:
            new_expire_dt = datetime.utcnow() + timedelta(days=duration_days)

        new_expire_at = new_expire_dt.isoformat() + 'Z'

        update_data = {
            'uuid': user.get('uuid'),
            'expireAt': new_expire_at,
            'status': 'ACTIVE'
        }

        _run_async(bypass_client._request('PATCH', '/api/users', update_data))

        logger.info(f"Bypass подписка для платежа {payment.payment_id} продлена на {duration_days} дней")
        return True

    except Exception as e:
        logger.error(f"Ошибка продления bypass подписки: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def send_key_to_user_sync(user_id: int, message_text: str, regular_vpn_key: str = None, night_key: str = None):
    """Отправляет ключ пользователю в Telegram."""
    import requests
    from config import BOT_TOKEN
    import json
    from pathlib import Path

    combined_message = message_text

    keyboard_buttons = []

    if night_key:
        keyboard_buttons.append([{"text": "🛡️ Открыть ключ ОБХОД глушилок + VPN", "url": night_key}])
    if regular_vpn_key:
        keyboard_buttons.append([{"text": "🌍 Открыть ключ обычного VPN", "url": regular_vpn_key}])

    keyboard_buttons.append([
        {"text": "⬅️ Главное меню", "callback_data": "main_menu"},
        {"text": "💬 Написать менеджеру", "url": "https://t.me/yamalube61"}
    ])

    keyboard = {"inline_keyboard": keyboard_buttons}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        data={
            'chat_id': user_id,
            'text': combined_message,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        },
        timeout=5
    )

    import time
    time.sleep(2)

    photo_path = Path("images/instruction.jpg")
    if photo_path.exists():
        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as f:
            requests.post(tg_url, files={'photo': f}, data={
                'chat_id': user_id,
                'caption': KEY_DELIVERY_MESSAGE_2,
                'parse_mode': 'HTML'
            }, timeout=10)
        time.sleep(1)
        kb_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    else:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                'chat_id': user_id,
                'text': KEY_DELIVERY_MESSAGE_2,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            },
            timeout=5
        )


def extract_key_from_response(response: dict) -> Optional[str]:
    """Извлекает subscription URL из ответа Remnawave API."""
    if 'subscriptionUrl' in response and response['subscriptionUrl']:
        return response['subscriptionUrl']
    if 'subscription_url' in response:
        return response['subscription_url']
    if 'url' in response:
        return response['url']
    if 'response' in response and isinstance(response['response'], dict):
        return extract_key_from_response(response['response'])
    return None


def extract_key_id_from_response(response: dict) -> Optional[str]:
    """Извлекает ID ключа из ответа API."""
    if 'id' in response:
        return str(response['id'])
    if 'key_id' in response:
        return str(response['key_id'])
    if 'response' in response and isinstance(response['response'], dict):
        return extract_key_id_from_response(response['response'])
    return None


FAST_VPN_DURATION_MAP = {
    'trial': 1,
    'week': 7,
    'month': 30,
    '3months': 90,
    '6months': 180,
    'year': 365,
}

FAST_VPN_TRAFFIC_MAP = {
    'trial': 10,
    'week': 120,
    'month': 120,
    '3months': 120,
    '6months': 120,
    'year': 120,
}


def process_fast_vpn_payment_sync(payment_id: int) -> dict:
    """
    Обработка платежа Обычный VPN через Remnawave Bypass API (1 squad).

    Создает пользователя в bypass Remnawave с одним squad'ом, получает subscriptionUrl как ключ.
    """
    try:
        payment = Payment.objects.select_related('user').get(payment_id=payment_id)
        logger.info(f"Обработка платежа Обычный VPN {payment_id}, подписка: {payment.subscription_type}")

        if payment.vpn_type != 'fast':
            return {'success': False, 'error': 'Не Обычный VPN платеж'}

        from .remnawave_api import get_remnawave_fast_vpn_client
        fast_client = get_remnawave_fast_vpn_client()
        if not fast_client:
            return {'success': False, 'error': 'Remnawave Обычный VPN API не настроен'}

        subscription_type = payment.subscription_type.replace('fast_', '')
        duration_days = FAST_VPN_DURATION_MAP.get(subscription_type, 30)
        user_id = payment.user.user_id

        remnawave_username = f"fast_{payment.user.username or f'user_{user_id}'}_{payment_id}_{int(datetime.utcnow().timestamp())}"

        remnawave_user = _run_async(
            fast_client.create_user(
                telegram_id=user_id,
                username=remnawave_username,
                expire_days=duration_days
            )
        )

        remnawave_user_uuid = remnawave_user.get('uuid')
        if not remnawave_user_uuid:
            raise RemnawaveAPIError("Не удалось получить UUID пользователя в Обычный VPN Remnawave")

        from datetime import timezone as dt_timezone
        expire_dt = datetime.now(dt_timezone.utc) + timedelta(days=duration_days)
        new_expire_at = expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{expire_dt.microsecond // 1000:03d}Z"

        traffic_gb = FAST_VPN_TRAFFIC_MAP.get(subscription_type)
        traffic_limit_bytes = traffic_gb * 1024 * 1024 * 1024 if traffic_gb else None

        update_data = {
            'uuid': remnawave_user_uuid,
            'expireAt': new_expire_at,
            'status': 'ACTIVE',
            'hwidDeviceLimit': 3,
        }
        if traffic_limit_bytes is not None:
            update_data['trafficLimitBytes'] = traffic_limit_bytes

        updated_user = _run_async(fast_client._request('PATCH', '/api/users', update_data))
        updated_user = updated_user.get('response', {})

        subscription_key = updated_user.get('subscriptionUrl') or remnawave_user.get('subscriptionUrl')

        if not subscription_key:
            raise RemnawaveAPIError(f"Не удалось извлечь subscription URL: {updated_user}")

        with transaction.atomic():
            payment.issued_key = subscription_key
            payment.status = 'succeeded'
            payment.paid_at = timezone.now()
            payment.subscription_expires_at = timezone.now() + timedelta(days=duration_days)
            payment.current_key_expires_at = payment.subscription_expires_at
            payment.fgcn_key_id = str(updated_user.get('id') or remnawave_user.get('id') or '')
            payment.fgcn_tg_id = user_id
            payment.is_fgn_key = True
            payment.bypass_remnawave_uuid = remnawave_user_uuid
            payment.save()

            if subscription_type == 'trial':
                try:
                    user = TelegramUser.objects.get(user_id=user_id)
                    user.trial_key_used_night = True
                    user.save()
                except TelegramUser.DoesNotExist:
                    pass

        logger.info(f"Платеж Обычный VPN {payment_id} успешно обработан, ключ: {subscription_key[:30]}...")

        try:
            from .referral_services import ReferralService
            from config import BOT_TOKEN
            from aiogram import Bot

            bot = Bot(token=BOT_TOKEN)
            referral_service = ReferralService(bot=bot)
            referral_service.process_referral_purchase_sync(user_id, payment)
        except Exception as e:
            logger.error(f"Ошибка обработки реферала: {e}")

        return {
            'success': True,
            'key_value': subscription_key,
            'fgcn_key_id': payment.fgcn_key_id,
        }

    except RemnawaveAPIError as e:
        logger.error(f"Ошибка Remnawave Bypass API при обработке Обычный VPN платежа {payment_id}: {e}")
        return {'success': False, 'error': f'Ошибка API: {str(e)}'}
    except Payment.DoesNotExist:
        logger.error(f"Платеж {payment_id} не найден")
        return {'success': False, 'error': 'Платеж не найден'}
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке Обычный VPN платежа {payment_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {'success': False, 'error': str(e)}


def process_fast_vpn_payment_sync_with_retry(payment_id: int, max_retries: int = 3, retry_delay: float = 2.0) -> dict:
    """
    Обработка платежа Обычный VPN с retry логикой.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Обычный VPN платеж {payment_id}: попытка {attempt}/{max_retries}")
            result = process_fast_vpn_payment_sync(payment_id)
            
            if result and result.get('success'):
                if attempt > 1:
                    logger.info(f"Обычный VPN платеж {payment_id}: успешно со {attempt}-й попытки")
                return result
            
            error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Пустой ответ'
            last_error = error_msg
            logger.warning(f"Обычный VPN платеж {payment_id}: попытка {attempt} неудачна: {error_msg}")
            
            if attempt < max_retries:
                logger.info(f"Обычный VPN платеж {payment_id}: ждём {retry_delay}с перед следующей попыткой")
                time.sleep(retry_delay)
                
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Обычный VPN платеж {payment_id}: попытка {attempt} исключение: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
    
    logger.error(f"Обычный VPN платеж {payment_id}: все {max_retries} попыток неудачны. Последняя ошибка: {last_error}")
    return {'success': False, 'error': f'API недоступен после {max_retries} попыток: {last_error}'}
