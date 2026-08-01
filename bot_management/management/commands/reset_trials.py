from django.core.management.base import BaseCommand
from django.db.models import Q
from bot_management.models import TelegramUser


class Command(BaseCommand):
    help = 'Сбросить флаги использования пробных ключей (trial)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='Telegram ID пользователя для сброса (если не указан — сброс для всех)'
        )
        parser.add_argument(
            '--vpn-type',
            type=str,
            choices=['night', 'regular', 'fast', 'all'],
            default='all',
            help='Тип VPN для сброса: night, regular, fast или all (по умолчанию: all)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет сброшено без фактического изменения'
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        vpn_type = options.get('vpn_type')
        dry_run = options.get('dry_run')

        if vpn_type == 'all':
            fields = ['trial_key_used_night', 'trial_key_used_regular', 'trial_key_used_fast']
        else:
            fields = [f'trial_key_used_{vpn_type}']

        if user_id:
            try:
                user = TelegramUser.objects.get(user_id=user_id)
            except TelegramUser.DoesNotExist:
                self.stderr.write(self.style.ERROR(f'Пользователь с ID {user_id} не найден'))
                return

            self.stdout.write(f'Пользователь: {user}')
            for field in fields:
                self.stdout.write(f'  {field}: {getattr(user, field)}')

            if dry_run:
                self.stdout.write(self.style.WARNING('Dry-run — изменения не применены'))
                return

            for field in fields:
                setattr(user, field, False)
            user.save(update_fields=fields)
            self.stdout.write(self.style.SUCCESS(f'Сброшены флаги для пользователя {user_id}'))
        else:
            q = Q()
            for field in fields:
                q |= Q(**{f'{field}': True})
            users = TelegramUser.objects.filter(q)
            count = users.count()

            if count == 0:
                self.stdout.write(self.style.WARNING('Нет пользователей с использованными пробниками'))
                return

            self.stdout.write(f'Найдено пользователей для сброса: {count}')

            if dry_run:
                self.stdout.write(self.style.WARNING('Dry-run — изменения не применены'))
                for user in users[:10]:
                    flags = ', '.join(f'{f}={getattr(user, f)}' for f in fields)
                    self.stdout.write(f'  {user} (ID: {user.user_id}) — {flags}')
                if count > 10:
                    self.stdout.write(f'  ... и ещё {count - 10}')
                return

            updated = TelegramUser.objects.filter(q).update(
                **{field: False for field in fields}
            )
            self.stdout.write(self.style.SUCCESS(f'Сброшены флаги для {updated} пользователей'))
