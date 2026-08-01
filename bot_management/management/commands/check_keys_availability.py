from django.core.management.base import BaseCommand
from django.db.models import Sum, F
from bot_management.models import SubscriptionKey
import logging
import asyncio
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Проверяет доступность VPN ключей и уведомляет админов при низком запасе'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет проверено без фактических уведомлений'
        )
        parser.add_argument(
            '--threshold',
            type=int,
            default=2,
            help='Порог количества ключей для уведомления (по умолчанию: 2)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        threshold = options['threshold']

        try:
            if dry_run:
                self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДВАРИТЕЛЬНОГО ПРОСМОТРА ==='))

            # Проверяем доступность ключей только для месячных подписок
            subscription_types = ['month']

            for subscription_type in subscription_types:
                self.check_keys_for_type(subscription_type, threshold, dry_run)

            if not dry_run:
                self.stdout.write(self.style.SUCCESS('Проверка доступности ключей завершена'))
            else:
                self.stdout.write(self.style.SUCCESS('Предварительный просмотр завершен'))

        except Exception as e:
            error_msg = f'Ошибка проверки доступности ключей: {e}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)
            raise

    def check_keys_for_type(self, subscription_type, threshold, dry_run=False):
        """Проверяет доступность ключей для конкретного типа подписки"""
        try:
            # Считаем доступные активации ключей
            available_count = SubscriptionKey.objects.filter(
                subscription_type=subscription_type,
                used_activations__lt=models.F('total_activations')
            ).aggregate(
                total_available=Sum('total_activations') - Sum('used_activations')
            )['total_available'] or 0

            self.stdout.write(f'Тип {subscription_type}: доступно {available_count} активаций')

            # Если осталось меньше порога, уведомляем админов
            if available_count < threshold:
                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Будет отправлено уведомление админам: '
                            f'осталось {available_count} активаций типа {subscription_type}'
                        )
                    )
                else:
                    # Запускаем асинхронное уведомление
                    asyncio.run(self.notify_admins_low_keys(subscription_type, available_count))
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Отправлено уведомление админам: '
                            f'осталось {available_count} активаций типа {subscription_type}'
                        )
                    )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f'Ошибка проверки ключей типа {subscription_type}: {e}'
                )
            )
            logger.error(f'Ошибка проверки ключей типа {subscription_type}: {e}')

    async def notify_admins_low_keys(self, subscription_type: str, available_count: int):
        """Уведомляет админов о низком количестве ключей"""
        try:
            from config import ADMIN_IDS

            # Определяем название типа подписки
            sub_names = {
                'month': 'Месячная',
                '3months': '3 месяца',
                '6months': '6 месяцев',
                'year': 'Годовая'
            }
            sub_name = sub_names.get(subscription_type, subscription_type)

            message = f"""
⚠️ <b>ВНИМАНИЕ! Низкий запас ключей</b>

📅 <b>Тип подписки:</b> {sub_name}
🔢 <b>Доступных активаций:</b> <code>{available_count}</code>

❌ <b>Рекомендуется пополнить склад ключей!</b>

💡 <i>Ключи заканчиваются, это может привести к проблемам с продажами.</i>
"""

            # Импортируем бота для отправки сообщений
            from bot_management.services import PaymentService
            payment_service = PaymentService()

            # Отправляем уведомление всем админам
            for admin_id in ADMIN_IDS:
                try:
                    await payment_service.bot.send_message(
                        admin_id,
                        message,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

            logger.info(f"Отправлено уведомление админам: осталось {available_count} активаций типа {subscription_type}")

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админам: {e}")
