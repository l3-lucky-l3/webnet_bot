from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import SupportChat, Payment, TelegramUser
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Проверка статуса системы'

    def handle(self, *args, **options):
        try:
            self.stdout.write(self.style.SUCCESS('=== СТАТУС СИСТЕМЫ ==='))
            
            # Статистика пользователей
            self.show_user_stats()
            
            # Статистика платежей
            self.show_payment_stats()
            
            # Статистика поддержки
            self.show_support_stats()
            
            # Проверка на проблемы
            self.check_issues()
            
            logger.info('Проверка статуса системы завершена')
            
        except Exception as e:
            error_msg = f'Ошибка проверки статуса: {e}'
            self.stdout.write(
                self.style.ERROR(error_msg)
            )
            logger.error(error_msg)
            raise

    def show_user_stats(self):
        """Статистика пользователей"""
        total_users = TelegramUser.objects.count()
        new_today = TelegramUser.objects.filter(
            created_at__date=timezone.now().date()
        ).count()
        
        self.stdout.write(f'\n👥 ПОЛЬЗОВАТЕЛИ:')
        self.stdout.write(f'  Всего: {total_users}')
        self.stdout.write(f'  Новых сегодня: {new_today}')

    def show_payment_stats(self):
        """Статистика платежей"""
        total_payments = Payment.objects.count()
        pending = Payment.objects.filter(status='pending').count()
        succeeded = Payment.objects.filter(status='succeeded').count()
        canceled = Payment.objects.filter(status='canceled').count()
        failed = Payment.objects.filter(status='failed').count()
        
        self.stdout.write(f'\n💳 ПЛАТЕЖИ:')
        self.stdout.write(f'  Всего: {total_payments}')
        self.stdout.write(f'  Ожидают оплаты: {pending}')
        self.stdout.write(f'  Успешных: {succeeded}')
        self.stdout.write(f'  Отмененных: {canceled}')
        self.stdout.write(f'  Неудачных: {failed}')

    def show_support_stats(self):
        """Статистика поддержки"""
        total_chats = SupportChat.objects.count()
        open_chats = SupportChat.objects.filter(status='open').count()
        closed_chats = SupportChat.objects.filter(status='closed').count()
        
        # Чаты с непрочитанными сообщениями
        unread_admin = SupportChat.objects.filter(unread_admin_messages__gt=0).count()
        unread_user = SupportChat.objects.filter(unread_user_messages__gt=0).count()
        
        self.stdout.write(f'\n💬 ПОДДЕРЖКА:')
        self.stdout.write(f'  Всего чатов: {total_chats}')
        self.stdout.write(f'  Открытых: {open_chats}')
        self.stdout.write(f'  Закрытых: {closed_chats}')
        self.stdout.write(f'  С непрочитанными (админ): {unread_admin}')
        self.stdout.write(f'  С непрочитанными (пользователь): {unread_user}')

    def check_issues(self):
        """Проверка на проблемы"""
        issues = []
        
        # Проверяем старые незакрытые чаты (старше 7 дней)
        old_open_chats = SupportChat.objects.filter(
            status='open',
            created_at__lt=timezone.now() - timedelta(days=7)
        ).count()
        
        if old_open_chats > 0:
            issues.append(f'⚠️  {old_open_chats} открытых чатов старше 7 дней')
        
        # Проверяем чаты с большим количеством непрочитанных сообщений
        many_unread = SupportChat.objects.filter(
            unread_admin_messages__gt=10
        ).count()
        
        if many_unread > 0:
            issues.append(f'⚠️  {many_unread} чатов с >10 непрочитанных сообщений')
        
        # Проверяем старые неудачные платежи
        old_failed_payments = Payment.objects.filter(
            status__in=['canceled', 'failed'],
            created_at__lt=timezone.now() - timedelta(days=30)
        ).count()
        
        if old_failed_payments > 0:
            issues.append(f'⚠️  {old_failed_payments} старых неудачных платежей')
        
        if issues:
            self.stdout.write(f'\n🚨 ПРОБЛЕМЫ:')
            for issue in issues:
                self.stdout.write(f'  {issue}')
        else:
            self.stdout.write(f'\n✅ Проблем не обнаружено')
