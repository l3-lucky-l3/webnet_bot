from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import SupportChat
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Автоматическая очистка закрытых чатов поддержки'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Количество дней после закрытия для удаления (по умолчанию: 7)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет удалено без фактического удаления'
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        
        try:
            # Находим закрытые чаты старше указанного количества дней
            cutoff_date = timezone.now() - timedelta(days=days)
            closed_chats = SupportChat.objects.filter(
                status='closed',
                created_at__lt=cutoff_date
            )
            
            count = closed_chats.count()
            
            if count == 0:
                self.stdout.write(
                    self.style.SUCCESS('Нет закрытых чатов для удаления')
                )
                logger.info('Автоочистка: нет чатов для удаления')
                return
            
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(f'Будет удалено {count} закрытых чатов:')
                )
                for chat in closed_chats:
                    self.stdout.write(f'  - {chat.ticket_number} ({chat.user}) - {chat.created_at}')
                logger.info(f'Автоочистка (dry-run): будет удалено {count} чатов')
            else:
                # Удаляем чаты
                deleted_count = 0
                for chat in closed_chats:
                    ticket_number = chat.ticket_number
                    user_info = str(chat.user)
                    chat.delete()
                    deleted_count += 1
                    self.stdout.write(f'Удален чат {ticket_number} ({user_info})')
                    logger.info(f'Автоочистка: удален чат {ticket_number} ({user_info})')
                
                self.stdout.write(
                    self.style.SUCCESS(f'Удалено {deleted_count} закрытых чатов')
                )
                logger.info(f'Автоочистка: удалено {deleted_count} чатов')
                
        except Exception as e:
            error_msg = f'Ошибка автоочистки: {e}'
            self.stdout.write(
                self.style.ERROR(error_msg)
            )
            logger.error(error_msg)
            raise
