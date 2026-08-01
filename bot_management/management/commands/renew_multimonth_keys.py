"""
Продление ключей для подписок на 3 месяца и год.
Находит платежи, у которых текущий месячный ключ истёк (current_key_expires_at <= now),
но подписка ещё действует (subscription_expires_at > now), и выдаёт новый месячный ключ из общей базы.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import Payment
from bot_management.services import PaymentService
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Продлевает месячные ключи для подписок на 3 месяца/год по истечении каждого месяца'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать, каким платежам будет выдан новый ключ, без фактической выдачи'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        # Платежи: успешные, 3м/6м/год, текущий ключ истёк, подписка ещё действует
        to_renew = Payment.objects.filter(
            status='succeeded',
            subscription_type__in=('3months', '6months', 'year'),
            current_key_expires_at__lte=now,
            subscription_expires_at__gt=now,
            issued_key__isnull=False
        ).select_related('user').order_by('current_key_expires_at')

        count = to_renew.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS('Нет платежей для продления ключей.'))
            return

        self.stdout.write(f'Найдено платежей для продления: {count}')

        service = PaymentService()
        renewed = 0
        no_key = 0

        for payment in to_renew:
            if dry_run:
                self.stdout.write(
                    f'  [dry-run] Платеж #{payment.payment_id} user_id={payment.user.user_id} '
                    f'current_key_expires_at={payment.current_key_expires_at}'
                )
                renewed += 1
                continue
            if service.renew_multimonth_key(payment):
                renewed += 1
                self.stdout.write(self.style.SUCCESS(f'  Продлён ключ для платежа #{payment.payment_id}'))
            else:
                no_key += 1
                self.stdout.write(self.style.WARNING(f'  Нет ключей для платежа #{payment.payment_id}'))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f'Продлено: {renewed}, без ключа: {no_key}'))
