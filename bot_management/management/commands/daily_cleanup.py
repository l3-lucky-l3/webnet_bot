from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import SupportChat, Payment
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ежедневная очистка системы'

    def add_arguments(self, parser):
        parser.add_argument(
            '--support-days',
            type=int,
            default=7,
            help='Дни для удаления закрытых чатов поддержки (по умолчанию: 7)'
        )
        parser.add_argument(
            '--payment-days',
            type=int,
            default=30,
            help='Дни для удаления старых неудачных платежей (по умолчанию: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет удалено без фактического удаления'
        )

    def handle(self, *args, **options):
        support_days = options['support_days']
        payment_days = options['payment_days']
        dry_run = options['dry_run']
        
        try:
            if dry_run:
                self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДВАРИТЕЛЬНОГО ПРОСМОТРА ==='))
            
            # Очистка закрытых чатов поддержки
            self.cleanup_support_chats(support_days, dry_run)
            
            # Очистка старых неудачных платежей
            self.cleanup_old_payments(payment_days, dry_run)
            
            if not dry_run:
                logger.info('Ежедневная очистка завершена успешно')
            else:
                self.stdout.write(self.style.SUCCESS('Предварительный просмотр завершен'))
            
        except Exception as e:
            error_msg = f'Ошибка ежедневной очистки: {e}'
            self.stdout.write(
                self.style.ERROR(error_msg)
            )
            logger.error(error_msg)
            raise

    def cleanup_support_chats(self, days, dry_run=False):
        """Очистка закрытых чатов поддержки"""
        try:
            cutoff_date = timezone.now() - timedelta(days=days)
            closed_chats = SupportChat.objects.filter(
                status='closed',
                created_at__lt=cutoff_date
            )
            
            count = closed_chats.count()
            
            if count > 0:
                if dry_run:
                    self.stdout.write(f'Будет удалено {count} закрытых чатов поддержки:')
                    for chat in closed_chats:
                        self.stdout.write(f'  - {chat.ticket_number} ({chat.user}) - {chat.created_at}')
                else:
                    closed_chats.delete()
                    self.stdout.write(
                        self.style.SUCCESS(f'Удалено {count} закрытых чатов поддержки')
                    )
                    logger.info(f'Удалено {count} закрытых чатов поддержки')
            else:
                self.stdout.write('Нет закрытых чатов для удаления')
                
        except Exception as e:
            logger.error(f'Ошибка очистки чатов поддержки: {e}')
            raise

    def cleanup_old_payments(self, days, dry_run=False):
        """Очистка старых неудачных платежей"""
        try:
            cutoff_date = timezone.now() - timedelta(days=days)
            old_payments = Payment.objects.filter(
                status__in=['canceled', 'failed'],
                created_at__lt=cutoff_date
            )
            
            count = old_payments.count()
            
            if count > 0:
                if dry_run:
                    self.stdout.write(f'Будет удалено {count} старых неудачных платежей:')
                    for payment in old_payments:
                        self.stdout.write(f'  - {payment.payment_id} ({payment.user}) - {payment.status} - {payment.created_at}')
                else:
                    old_payments.delete()
                    self.stdout.write(
                        self.style.SUCCESS(f'Удалено {count} старых неудачных платежей')
                    )
                    logger.info(f'Удалено {count} старых неудачных платежей')
            else:
                self.stdout.write('Нет старых платежей для удаления')
                
        except Exception as e:
            logger.error(f'Ошибка очистки платежей: {e}')
            raise
