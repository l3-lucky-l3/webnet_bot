from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Sum, Count, Q
from datetime import date, datetime
from bot_management.models import Payment
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Показывает статистику платежей только за сегодня'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Дата в формате YYYY-MM-DD (по умолчанию: сегодня)',
            default=None
        )

    def handle(self, *args, **options):
        try:
            # Определяем дату для анализа
            if options['date']:
                target_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            else:
                target_date = timezone.now().date()

            self.stdout.write(
                self.style.SUCCESS(f'📊 СТАТИСТИКА ПЛАТЕЖЕЙ ЗА {target_date} 📊\n')
            )

            # Получаем начало и конец дня
            start_of_day = timezone.datetime.combine(target_date, timezone.datetime.min.time())
            end_of_day = timezone.datetime.combine(target_date, timezone.datetime.max.time())

            # Фильтруем платежи за указанный день
            payments_today = Payment.objects.filter(
                Q(paid_at__date=target_date) |  # Оплаченные сегодня
                Q(status='succeeded', paid_at__isnull=True, created_at__date=target_date)  # Успешные без paid_at
            ).exclude(status='canceled')  # Исключаем отмененные

            # Общая статистика
            total_payments = payments_today.count()
            total_amount = payments_today.filter(status='succeeded').aggregate(
                total=Sum('amount')
            )['total'] or 0

            # Статистика по статусам
            status_stats = payments_today.values('status').annotate(
                count=Count('status'),
                amount=Sum('amount')
            ).order_by('-count')

            # Статистика по типам подписок
            subscription_stats = payments_today.filter(status='succeeded').values('subscription_type').annotate(
                count=Count('subscription_type'),
                amount=Sum('amount')
            ).order_by('-amount')

            # Вывод общей статистики
            self.stdout.write('📈 ОБЩАЯ СТАТИСТИКА:')
            self.stdout.write(f'• Всего платежей: {total_payments}')
            self.stdout.write(f'• Общая сумма: {total_amount}₽')
            self.stdout.write('')

            # Вывод статистики по статусам
            self.stdout.write('📋 ПО СТАТУСАМ:')
            status_names = {
                'succeeded': '✅ Оплаченные',
                'pending': '⏳ Ожидают оплаты',
                'failed': '❌ Ошибки оплаты',
                'canceled': '🚫 Отмененные'
            }

            for stat in status_stats:
                status_name = status_names.get(stat['status'], stat['status'])
                count = stat['count']
                amount = stat['amount'] or 0
                self.stdout.write(f'• {status_name}: {count} шт. ({amount}₽)')
            self.stdout.write('')

            # Вывод статистики по типам подписок
            self.stdout.write('🛒 ПО ТИПАМ ПОДПИСОК:')
            subscription_names = {
                'trial': '🎁 Пробная',
                'week': '📅 Недельная',
                'month': '📅 Месячная',
                '3months': '📅 3 месяца',
                '6months': '📅 6 месяцев',
                'year': '📅 Годовая'
            }

            for stat in subscription_stats:
                sub_type = stat['subscription_type']
                sub_name = subscription_names.get(sub_type, sub_type)
                count = stat['count']
                amount = stat['amount'] or 0
                self.stdout.write(f'• {sub_name}: {count} шт. ({amount}₽)')
            self.stdout.write('')

            # Детализация успешных платежей
            successful_payments = payments_today.filter(status='succeeded').order_by('-paid_at')
            if successful_payments.exists():
                self.stdout.write('💰 ДЕТАЛИ УСПЕШНЫХ ПЛАТЕЖЕЙ:')
                for payment in successful_payments[:10]:  # Показываем первые 10
                    user_info = f"@{payment.user.username}" if payment.user.username else f"ID{payment.user.user_id}"
                    paid_time = payment.paid_at.strftime('%H:%M') if payment.paid_at else 'время неизвестно'
                    sub_name = subscription_names.get(payment.subscription_type, payment.subscription_type)
                    self.stdout.write(f'• {payment.amount}₽ - {sub_name} - {user_info} ({paid_time})')

                if successful_payments.count() > 10:
                    self.stdout.write(f'... и ещё {successful_payments.count() - 10} платежей')
                self.stdout.write('')

            # Сравнение с предыдущим днем (если есть данные)
            yesterday = target_date - timezone.timedelta(days=1)
            yesterday_payments = Payment.objects.filter(
                Q(paid_at__date=yesterday) |
                Q(status='succeeded', paid_at__isnull=True, created_at__date=yesterday)
            ).exclude(status='canceled')

            yesterday_amount = yesterday_payments.filter(status='succeeded').aggregate(
                total=Sum('amount')
            )['total'] or 0

            if yesterday_amount > 0:
                change = total_amount - yesterday_amount
                change_percent = (change / yesterday_amount) * 100 if yesterday_amount > 0 else 0

                change_symbol = '📈' if change >= 0 else '📉'
                change_text = f"{change:+}₽ ({change_percent:+.1f}%)"

                self.stdout.write('📊 СРАВНЕНИЕ С ВЧЕРА:')
                self.stdout.write(f'• Вчера: {yesterday_amount}₽')
                self.stdout.write(f'• Сегодня: {total_amount}₽')
                self.stdout.write(f'• Изменение: {change_symbol} {change_text}')

            self.stdout.write(self.style.SUCCESS('✅ Статистика сформирована'))

        except Exception as e:
            error_msg = f'Ошибка получения статистики платежей: {e}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)
            raise



