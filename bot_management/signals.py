"""
Сигналы Django для обработки событий модели Payment
"""

import asyncio
import logging
from datetime import timedelta
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Payment

logger = logging.getLogger(__name__)

# Словарь для хранения отложенных задач уведомлений
# Ключ: payment_id, Значение: asyncio.Task
_scheduled_notifications = {}

@receiver(post_save, sender=Payment)
def schedule_trial_key_expiry_notification(sender, instance, created, **kwargs):
    """
    Обработчик сигнала для Payment модели.
    Запускает отложенное уведомление админам через 24 часа после истечения trial ключа.
    """
    # Проверяем условия:
    # 1. Это trial платеж
    # 2. Статус успешный
    # 3. Ключ выдан
    # 4. Уведомление еще не отправлено
    # 5. Ключ только что истек или вот-вот истечет

    if (instance.subscription_type == 'trial' and
        instance.status == 'succeeded' and
        instance.issued_key and
        not instance.trial_key_expired_admin_notified):

        now = timezone.now()
        expires_at = instance.subscription_expires_at

        if expires_at:
            # Проверяем, истек ли ключ в последние 5 минут
            # Это даст небольшое окно для планирования уведомления
            time_since_expiry = now - expires_at

            # Если ключ истек менее 5 минут назад, планируем уведомление через 24 часа
            if timedelta(minutes=-5) <= time_since_expiry <= timedelta(minutes=5):
                # Отменяем предыдущую задачу если она существует
                if instance.payment_id in _scheduled_notifications:
                    _scheduled_notifications[instance.payment_id].cancel()
                    del _scheduled_notifications[instance.payment_id]

                # Создаем новую отложенную задачу
                notification_time = expires_at + timedelta(hours=24)
                delay_seconds = (notification_time - now).total_seconds()

                if delay_seconds > 0:
                    task = asyncio.create_task(
                        _delayed_trial_key_notification(instance.payment_id, delay_seconds)
                    )
                    _scheduled_notifications[instance.payment_id] = task

                    logger.info(
                        f"Запланировано уведомление о trial ключе {instance.issued_key} "
                        f"через {delay_seconds:.0f} секунд (в {notification_time})"
                    )
                else:
                    logger.warning(
                        f"Trial ключ {instance.issued_key} уже должен был быть обработан "
                        f"(задержка: {abs(delay_seconds):.0f} секунд)"
                    )

async def _delayed_trial_key_notification(payment_id: int, delay_seconds: float):
    """
    Отложенная отправка уведомления о закончившемся trial ключе
    """
    try:
        # Ждем указанное время
        await asyncio.sleep(delay_seconds)

        # Получаем свежие данные платежа
        try:
            payment = await Payment.objects.aget(pk=payment_id)
        except Payment.DoesNotExist:
            logger.error(f"Платеж {payment_id} не найден при отправке уведомления")
            return

        # Проверяем, не отправлено ли уже уведомление
        if payment.trial_key_expired_admin_notified:
            logger.info(f"Уведомление о trial ключе {payment.issued_key} уже отправлено")
            return

        # Отправляем уведомление
        success = await _send_trial_key_expired_notification(payment)

        if success:
            # Помечаем как уведомлено
            payment.trial_key_expired_admin_notified = True
            await payment.asave(update_fields=['trial_key_expired_admin_notified'])

            logger.info(f"Отправлено уведомление о trial ключе {payment.issued_key}")
        else:
            logger.error(f"Ошибка отправки уведомления о trial ключе {payment.issued_key}")

        # Убираем задачу из словаря
        if payment_id in _scheduled_notifications:
            del _scheduled_notifications[payment_id]

    except asyncio.CancelledError:
        logger.info(f"Уведомление о trial ключе платежа {payment_id} отменено")
    except Exception as e:
        logger.error(f"Ошибка в отложенном уведомлении о trial ключе {payment_id}: {e}")

async def _send_trial_key_expired_notification(payment: Payment) -> bool:
    """
    Отправляет уведомление админам об истекшем trial ключе
    """
    try:
        from config import ADMIN_IDS

        user_info = f"@{payment.user.username}" if payment.user.username else f"ID{payment.user.user_id}"

        # Вычисляем сколько часов прошло с момента истечения
        hours_expired = int((timezone.now() - payment.subscription_expires_at).total_seconds() / 3600)

        message = f"""
⚠️ <b>ВНИМАНИЕ! Trial ключ закончился</b>

🎁 <b>Trial подписка истекла:</b>
└ <code>{payment.issued_key}</code>

👤 <b>Пользователь:</b> {user_info}
⏰ <b>Истек:</b> {payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M')}
🕐 <b>Прошло часов:</b> {hours_expired}

💡 <b>Действие:</b> Рекомендуется отключить этот ключ в админ-панели
🔧 <b>Причина:</b> Trial ключи выдаются только на 1 день
"""

        # Отправляем уведомление каждому админу
        success_count = 0
        for admin_id in ADMIN_IDS:
            try:
                await _send_telegram_message(admin_id, message)
                success_count += 1
                logger.info(f'Отправлено уведомление админу {admin_id} о trial ключе {payment.issued_key}')
            except Exception as e:
                logger.error(f'Ошибка отправки уведомления админу {admin_id}: {e}')

        return success_count > 0

    except Exception as e:
        logger.error(f'Ошибка уведомления админов о trial ключе {payment.issued_key}: {e}')
        return False

async def _send_telegram_message(chat_id: int, text: str):
    """Отправляет сообщение в Telegram"""
    import requests

    try:
        from django.conf import settings
        token = settings.BOT_TOKEN
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        async with requests.AsyncSession() as session:
            async with session.post(url, json=data) as response:
                if response.status_code != 200:
                    raise Exception(f"Telegram API error: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Ошибка отправки сообщения в Telegram: {e}")
        raise


def cancel_scheduled_notifications():
    """
    Отменяет все запланированные уведомления (для graceful shutdown)
    """
    for payment_id, task in _scheduled_notifications.items():
        if not task.done():
            task.cancel()
            logger.info(f"Отменено уведомление для платежа {payment_id}")

    _scheduled_notifications.clear()
