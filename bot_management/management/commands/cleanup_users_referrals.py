"""
Команда для очистки пользователей и рефералов из БД
Использование: python manage.py cleanup_users_referrals [--dry-run] [--days N] [--no-payments]
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import TelegramUser, Payment
from bot_management.referral_models import Referral, ReferralCode, ReferralReward
from bot_management.models import ReferralBalanceTransaction
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Очистка пользователей и рефералов из БД'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет удалено без фактического удаления',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Удалить пользователей без активности за N дней (по умолчанию 30)',
        )
        parser.add_argument(
            '--no-payments',
            action='store_true',
            help='Удалять только пользователей без платежей',
        )
        parser.add_argument(
            '--inactive-referrals',
            action='store_true',
            help='Удалить неактивные реферальные связи',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='⚠️ ОПАСНО: Полностью удалить ВСЕХ пользователей и ВСЕ реферальные связи',
        )
        parser.add_argument(
            '--all-referrals',
            action='store_true',
            help='Удалить ВСЕ реферальные связи (активные и неактивные)',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='⚠️ ОПАСНО: Удалить ВСЕХ пользователей (включая с платежами)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days = options['days']
        no_payments_only = options['no_payments']
        inactive_referrals = options['inactive_referrals']
        delete_all = options['all']
        delete_all_referrals = options['all_referrals']
        delete_all_users = options['all_users']
        
        self.stdout.write(self.style.WARNING('=' * 60))
        self.stdout.write(self.style.WARNING('ОЧИСТКА БАЗЫ ДАННЫХ'))
        self.stdout.write(self.style.WARNING('=' * 60))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('РЕЖИМ ПРОСМОТРА (dry-run) - изменения не будут применены'))
        
        # Предупреждение для полного удаления
        if delete_all or delete_all_users:
            self.stdout.write(self.style.ERROR('=' * 60))
            self.stdout.write(self.style.ERROR('⚠️  ВНИМАНИЕ: БУДЕТ ВЫПОЛНЕНО ПОЛНОЕ УДАЛЕНИЕ!'))
            self.stdout.write(self.style.ERROR('=' * 60))
            if not dry_run:
                import time
                self.stdout.write(self.style.WARNING('Пауза 5 секунд для отмены (Ctrl+C)...'))
                try:
                    time.sleep(5)
                except KeyboardInterrupt:
                    self.stdout.write(self.style.SUCCESS('Операция отменена'))
                    return
        
        deleted_count = 0
        
        # 1. Удаление неактивных реферальных связей
        if inactive_referrals:
            self.stdout.write('\n1. Удаление неактивных реферальных связей...')
            inactive_referrals_count = Referral.objects.filter(is_active=False).count()
            if inactive_referrals_count > 0:
                self.stdout.write(f'   Найдено неактивных реферальных связей: {inactive_referrals_count}')
                if not dry_run:
                    deleted = Referral.objects.filter(is_active=False).delete()
                    deleted_count += deleted[0]
                    self.stdout.write(self.style.SUCCESS(f'   Удалено: {deleted[0]}'))
                else:
                    self.stdout.write(f'   Будет удалено: {inactive_referrals_count}')
            else:
                self.stdout.write('   Неактивных реферальных связей не найдено')
        
        # 2. Удаление пользователей без платежей и активности
        if no_payments_only:
            self.stdout.write('\n2. Поиск пользователей без платежей...')
            cutoff_date = timezone.now() - timedelta(days=days)
            
            # Пользователи без платежей и без активности
            users_without_payments = TelegramUser.objects.filter(
                payments__isnull=True
            ).exclude(
                user_id__in=Payment.objects.values_list('user_id', flat=True).distinct()
            )
            
            # Пользователи без активности за N дней
            inactive_users = users_without_payments.filter(
                created_at__lt=cutoff_date
            )
            
            inactive_count = inactive_users.count()
            
            if inactive_count > 0:
                self.stdout.write(f'   Найдено неактивных пользователей без платежей: {inactive_count}')
                
                if not dry_run:
                    # Удаляем связанные данные
                    user_ids = list(inactive_users.values_list('user_id', flat=True))
                    
                    # Удаляем реферальные связи
                    Referral.objects.filter(referred_id__in=user_ids).delete()
                    Referral.objects.filter(referrer_id__in=user_ids).delete()
                    
                    # Удаляем реферальные коды
                    ReferralCode.objects.filter(user_id__in=user_ids).delete()
                    
                    # Удаляем награды
                    ReferralReward.objects.filter(referral__referrer_id__in=user_ids).delete()
                    ReferralReward.objects.filter(referral__referred_id__in=user_ids).delete()
                    
                    # Удаляем транзакции
                    ReferralBalanceTransaction.objects.filter(user_id__in=user_ids).delete()
                    
                    # Удаляем пользователей
                    deleted = inactive_users.delete()
                    deleted_count += deleted[0]
                    self.stdout.write(self.style.SUCCESS(f'   Удалено пользователей: {deleted[0]}'))
                else:
                    self.stdout.write(f'   Будет удалено пользователей: {inactive_count}')
                    # Показываем примеры
                    sample_users = inactive_users[:5]
                    for user in sample_users:
                        self.stdout.write(f'      - ID{user.user_id} (создан: {user.created_at.strftime("%d.%m.%Y")})')
            else:
                self.stdout.write('   Неактивных пользователей без платежей не найдено')
        
        # 3. Удаление реферальных связей с несуществующими пользователями
        self.stdout.write('\n3. Проверка реферальных связей с несуществующими пользователями...')
        
        # Рефералы, у которых реферер не существует
        referrals_with_missing_referrer = Referral.objects.exclude(
            referrer_id__in=TelegramUser.objects.values_list('user_id', flat=True)
        )
        missing_referrer_count = referrals_with_missing_referrer.count()
        
        # Рефералы, у которых реферал не существует
        referrals_with_missing_referred = Referral.objects.exclude(
            referred_id__in=TelegramUser.objects.values_list('user_id', flat=True)
        )
        missing_referred_count = referrals_with_missing_referred.count()
        
        total_missing = missing_referrer_count + missing_referred_count
        
        if total_missing > 0:
            self.stdout.write(f'   Найдено реферальных связей с несуществующими пользователями: {total_missing}')
            if not dry_run:
                deleted = referrals_with_missing_referrer.delete()
                deleted_count += deleted[0]
                deleted = referrals_with_missing_referred.delete()
                deleted_count += deleted[0]
                self.stdout.write(self.style.SUCCESS(f'   Удалено реферальных связей: {total_missing}'))
            else:
                self.stdout.write(f'   Будет удалено реферальных связей: {total_missing}')
        else:
            self.stdout.write('   Реферальных связей с несуществующими пользователями не найдено')
        
        # 4. Удаление реферальных кодов для несуществующих пользователей
        self.stdout.write('\n4. Проверка реферальных кодов для несуществующих пользователей...')
        codes_with_missing_users = ReferralCode.objects.exclude(
            user_id__in=TelegramUser.objects.values_list('user_id', flat=True)
        )
        missing_codes_count = codes_with_missing_users.count()
        
        if missing_codes_count > 0:
            self.stdout.write(f'   Найдено реферальных кодов для несуществующих пользователей: {missing_codes_count}')
            if not dry_run:
                deleted = codes_with_missing_users.delete()
                deleted_count += deleted[0]
                self.stdout.write(self.style.SUCCESS(f'   Удалено реферальных кодов: {deleted[0]}'))
            else:
                self.stdout.write(f'   Будет удалено реферальных кодов: {missing_codes_count}')
        else:
            self.stdout.write('   Реферальных кодов для несуществующих пользователей не найдено')
        
        # 5. ПОЛНОЕ УДАЛЕНИЕ ВСЕХ РЕФЕРАЛЬНЫХ СВЯЗЕЙ
        if delete_all_referrals or delete_all:
            self.stdout.write('\n5. ПОЛНОЕ УДАЛЕНИЕ ВСЕХ РЕФЕРАЛЬНЫХ СВЯЗЕЙ...')
            all_referrals_count = Referral.objects.count()
            if all_referrals_count > 0:
                self.stdout.write(f'   Найдено реферальных связей: {all_referrals_count}')
                if not dry_run:
                    # Удаляем все связанные данные
                    # Сначала удаляем награды
                    ReferralReward.objects.all().delete()
                    # Затем удаляем реферальные связи
                    deleted = Referral.objects.all().delete()
                    deleted_count += deleted[0]
                    self.stdout.write(self.style.SUCCESS(f'   Удалено реферальных связей: {deleted[0]}'))
                else:
                    self.stdout.write(f'   Будет удалено реферальных связей: {all_referrals_count}')
            else:
                self.stdout.write('   Реферальных связей не найдено')
        
        # 6. ПОЛНОЕ УДАЛЕНИЕ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
        if delete_all_users or delete_all:
            self.stdout.write('\n6. ПОЛНОЕ УДАЛЕНИЕ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ...')
            all_users_count = TelegramUser.objects.count()
            if all_users_count > 0:
                self.stdout.write(f'   Найдено пользователей: {all_users_count}')
                if not dry_run:
                    # Получаем все ID пользователей
                    user_ids = list(TelegramUser.objects.values_list('user_id', flat=True))
                    
                    # Удаляем все связанные данные
                    # Реферальные связи (если еще не удалены)
                    Referral.objects.filter(referrer_id__in=user_ids).delete()
                    Referral.objects.filter(referred_id__in=user_ids).delete()
                    
                    # Реферальные коды
                    ReferralCode.objects.filter(user_id__in=user_ids).delete()
                    
                    # Награды
                    ReferralReward.objects.filter(referral__referrer_id__in=user_ids).delete()
                    ReferralReward.objects.filter(referral__referred_id__in=user_ids).delete()
                    
                    # Транзакции реферального баланса
                    ReferralBalanceTransaction.objects.filter(user_id__in=user_ids).delete()
                    
                    # Платежи (опционально, можно закомментировать если нужно сохранить историю платежей)
                    # Payment.objects.filter(user_id__in=user_ids).delete()
                    
                    # Пользователи
                    deleted = TelegramUser.objects.all().delete()
                    deleted_count += deleted[0]
                    self.stdout.write(self.style.SUCCESS(f'   Удалено пользователей: {deleted[0]}'))
                else:
                    self.stdout.write(f'   Будет удалено пользователей: {all_users_count}')
            else:
                self.stdout.write('   Пользователей не найдено')
        
        # Итоги
        self.stdout.write('\n' + '=' * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING(f'РЕЖИМ ПРОСМОТРА: будет удалено записей: {deleted_count}'))
            self.stdout.write(self.style.WARNING('Для фактического удаления запустите без --dry-run'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Очистка завершена. Удалено записей: {deleted_count}'))
        self.stdout.write('=' * 60)

