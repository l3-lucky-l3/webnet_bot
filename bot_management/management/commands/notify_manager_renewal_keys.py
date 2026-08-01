"""
Уведомление менеджеру за день до необходимости выдать ключи на продление.
Считает, сколько подписок (3 мес/год) завтра переходят на следующий месяц и требуют новый ключ из базы.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import Payment
from config import ADMIN_IDS
import logging
import asyncio
from aiogram import Bot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправляет менеджеру уведомление: сколько ключей нужно добавить завтра на продление'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только вывести число и текст без отправки в Telegram'
        )
        parser.add_argument(
            '--hours-ahead',
            type=int,
            default=24,
            help='За сколько часов до момента продления уведомлять (по умолчанию: 24)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        hours_ahead = options['hours_ahead']

        now = timezone.now()
        # Окно «завтра»: через hours_ahead от сейчас и до конца следующих 24 ч
        window_start = now + timedelta(hours=hours_ahead)
        window_end = window_start + timedelta(hours=24)

        # Платежи, у которых текущий ключ истекает завтра (им нужен новый ключ из базы)
        need_renewal = Payment.objects.filter(
            status='succeeded',
            subscription_type__in=('3months', '6months', 'year'),
            current_key_expires_at__gte=window_start,
            current_key_expires_at__lt=window_end,
            subscription_expires_at__gt=window_start,
            issued_key__isnull=False
        ).count()

        if need_renewal == 0:
            self.stdout.write(self.style.SUCCESS(
                f'На завтра ({window_start.strftime("%d.%m.%Y %H:%M")}) продлений не запланировано.'
            ))
            return

        text = f"""📌 <b>Напоминание менеджеру: ключи на продление</b>

📅 <b>Завтра</b> (в течение дня) истекает текущий месячный ключ у <b>{need_renewal}</b> подписок (3 мес / год).

✅ Нужно добавить в базу <b>не менее {need_renewal} месячных ключей</b>, чтобы продление прошло автоматически.

💡 Добавляйте ключи в разделе «Месячная» — они используются и для продления 3-месячных и годовых подписок."""

        self.stdout.write(text.replace('<b>', '').replace('</b>', '').replace('<br>', '\n'))

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: уведомление не отправлено.'))
            return

        if not ADMIN_IDS:
            self.stdout.write(self.style.WARNING('ADMIN_IDS не заданы, уведомление не отправлено.'))
            return

        asyncio.run(self._send_to_admins(text))

    async def _send_to_admins(self, text: str):
        from config import BOT_TOKEN
        bot = Bot(token=BOT_TOKEN)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, parse_mode='HTML')
                logger.info(f'Уведомление о ключах на продление отправлено админу {admin_id}')
            except Exception as e:
                logger.error(f'Не удалось отправить уведомление админу {admin_id}: {e}')
