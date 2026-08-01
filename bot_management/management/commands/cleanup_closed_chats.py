from django.core.management.base import BaseCommand
from bot_management.models import SupportChat
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Удаляет закрытые чаты поддержки старше 7 дней'

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
            return
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'Будет удалено {count} закрытых чатов:')
            )
            for chat in closed_chats:
                self.stdout.write(f'  - {chat.ticket_number} ({chat.user}) - {chat.created_at}')
        else:
            # Удаляем чаты
            deleted_count = 0
            for chat in closed_chats:
                ticket_number = chat.ticket_number
                user_info = str(chat.user)
                chat.delete()
                deleted_count += 1
                self.stdout.write(f'Удален чат {ticket_number} ({user_info})')
            
            self.stdout.write(
                self.style.SUCCESS(f'Удалено {deleted_count} закрытых чатов')
            )
