from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import Payment, SubscriptionKey
import logging
import asyncio
import aiohttp
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправляет уведомления админам о закончившихся trial ключах (через сутки после истечения)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет отправлено без фактической отправки'
        )
        parser.add_argument(
            '--hours-after-expiry',
            type=int,
            default=24,
            help='Часы после истечения trial ключа для отправки уведомления админу (по умолчанию: 24)'
        )
        parser.add_argument(
            '--tolerance-hours',
            type=int,
            default=1,
            help='Окно погрешности в часах для проверки (по умолчанию: 1 час)'
        )
        parser.add_argument(
            '--reset-flags',
            action='store_true',
            help='Сбросить флаги отправленных уведомлений для повторной отправки'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # Старые параметры оставлены для совместимости, но логика упрощена:
        # теперь берем все trial-ключи, у которых подписка уже истекла.
        reset_flags = options['reset_flags']

        try:
            if reset_flags:
                reset_count = Payment.objects.filter(
                    trial_key_expired_admin_notified=True,
                    subscription_type='trial'
                ).update(trial_key_expired_admin_notified=False)
                self.stdout.write(
                    self.style.WARNING(f'Сброшены флаги уведомлений для {reset_count} trial ключей')
                )

            if dry_run:
                self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДВАРИТЕЛЬНОГО ПРОСМОТРА ==='))

            now = timezone.now()

            # Берём все trial-платежи, у которых подписка уже истекла,
            # уведомление ещё не отправлялось и ключ реально был выдан.
            expired_trial_keys = Payment.objects.filter(
                subscription_type='trial',
                status='succeeded',
                subscription_expires_at__lte=now,
                trial_key_expired_admin_notified=False,
                issued_key__isnull=False
            ).select_related('user').order_by('subscription_expires_at')

            if not expired_trial_keys.exists():
                self.stdout.write(self.style.SUCCESS('Нет trial ключей, требующих уведомления админам'))
                return

            # Группируем по самому trial-ключу: уведомление одно на ключ, даже если активаций/платежей несколько
            unique_keys = {}
            for payment in expired_trial_keys:
                if payment.issued_key not in unique_keys:
                    unique_keys[payment.issued_key] = payment

            self.stdout.write(f'Найдено {len(unique_keys)} уникальных trial ключей для проверки')

            notified_count = 0
            for issued_key, payment in unique_keys.items():
                # Ищем сам trial-ключ в базе
                key = SubscriptionKey.objects.filter(
                    key_value=issued_key,
                    subscription_type='trial'
                ).first()

                if not key:
                    # Ключ не найден — логируем и пропускаем
                    logger.warning(f'Trial ключ {issued_key} не найден в SubscriptionKey')
                    continue

                # Если у trial-ключа ещё есть свободные активации и он активен, уведомление не шлём
                remaining = key.total_activations - key.used_activations
                if key.is_active and remaining > 0:
                    continue

                # К этому моменту trial-ключ полностью исчерпан (или деактивирован) — можно уведомлять
                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Будет отправлено уведомление админам о полностью исчерпанном trial ключе: {issued_key} '
                            f'(истек {payment.subscription_expires_at}, пользователь: @{payment.user.username or payment.user.user_id})'
                        )
                    )
                else:
                    success = asyncio.run(self.notify_admins_trial_key_expired(payment))
                    if success:
                        payment.trial_key_expired_admin_notified = True
                        payment.save(update_fields=['trial_key_expired_admin_notified'])
                        notified_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Отправлено уведомление о полностью исчерпанном trial ключе: {issued_key}'
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.ERROR(
                                f'Ошибка отправки уведомления о trial ключе: {issued_key}'
                            )
                        )

            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f'Отправлено уведомлений админам: {notified_count}')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f'Будет отправлено уведомлений: {len(expired_trial_keys)}')
                )

        except Exception as e:
            error_msg = f'Ошибка отправки уведомлений о trial ключах: {e}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)
            raise

    async def notify_admins_trial_key_expired(self, payment: Payment) -> bool:
        """Уведомляет админов об истекшем trial ключе"""
        try:
            from config import ADMIN_IDS

            if payment.user.username:
                user_info = f"@{payment.user.username}"
            else:
                user_info = f"ID{payment.user.user_id}"

            hours_expired = int((timezone.now() - payment.subscription_expires_at).total_seconds() / 3600)

            message = f"""⚠️ <b>ВНИМАНИЕ! Trial ключ закончился</b>

🎁 <b>Trial подписка истекла:</b>
└ <code>{payment.issued_key}</code>

👤 <b>Пользователь:</b> {user_info}
⏰ <b>Истек:</b> {payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M')}
🕐 <b>Прошло часов:</b> {hours_expired}

💡 <b>Действие:</b> Рекомендуется отключить этот ключ в админ-панели
🔧 <b>Причина:</b> Trial ключи выдаются только на 1 день"""

            success_count = 0
            for admin_id in ADMIN_IDS:
                try:
                    await self.send_telegram_message(admin_id, message)
                    success_count += 1
                    logger.info(f'Отправлено уведомление админу {admin_id} о trial ключе {payment.issued_key}')
                except Exception as e:
                    logger.error(f'Ошибка отправки уведомления админу {admin_id}: {e}')

            return success_count > 0

        except Exception as e:
            logger.error(f'Ошибка уведомления админов о trial ключе {payment.issued_key}: {e}')
            return False

    async def send_telegram_message(self, chat_id: int, text: str):
        """Отправляет сообщение в Telegram через aiohttp"""
        token = settings.BOT_TOKEN
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, timeout=10) as response:
                if response.status != 200:
                    body = await response.text()
                    raise Exception(f"Telegram API error: {response.status} - {body}")

