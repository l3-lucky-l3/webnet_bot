from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import Payment
import logging
import requests
import json
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправляет напоминания о незавершенных платежах старше 30 минут'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет отправлено без фактической отправки'
        )
        parser.add_argument(
            '--minutes',
            type=int,
            default=30,
            help='Минуты после создания платежа для отправки напоминания (по умолчанию: 30)'
        )
        parser.add_argument(
            '--max-days',
            type=int,
            default=3,
            help='Максимальный возраст платежа в днях для отправки напоминания (по умолчанию: 3)'
        )
        parser.add_argument(
            '--reset-flags',
            action='store_true',
            help='Сбросить флаги отправленных напоминаний для повторной отправки'
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        minutes = options['minutes']
        max_days = options['max_days']
        reset_flags = options['reset_flags']

        try:
            # Сброс флагов отправленных напоминаний
            if reset_flags:
                reset_count = Payment.objects.filter(reminder_sent=True).update(reminder_sent=False)
                self.stdout.write(
                    self.style.SUCCESS(f'Сброшены флаги напоминаний для {reset_count} платежей')
                )
                return

            if dry_run:
                self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДВАРИТЕЛЬНОГО ПРОСМОТРА ==='))

            # Находим просроченные платежи (не старше max_days дней)
            cutoff_time = timezone.now() - timedelta(minutes=minutes)
            max_age_cutoff = timezone.now() - timedelta(days=max_days)

            expired_payments = Payment.objects.filter(
                status='pending',
                created_at__lt=cutoff_time,
                created_at__gte=max_age_cutoff,  # Не старше max_days дней
                reminder_sent=False  # Только платежи, по которым не отправлялись напоминания
            ).select_related('user')

            count = expired_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} просроченных платежей')

                for payment in expired_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                f'Будет отправлено напоминание пользователю {payment.user.user_id} '
                                f'о платеже {payment.payment_id} ({payment.amount}₽, {payment.subscription_type})'
                            )
                        else:
                            self.send_reminder(payment)
                            # Помечаем, что напоминание отправлено
                            payment.reminder_sent = True
                            payment.save(update_fields=['reminder_sent'])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено напоминание пользователю {payment.user.user_id} '
                                    f'о платеже {payment.payment_id}'
                                )
                            )
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(
                                f'Ошибка отправки напоминания для платежа {payment.payment_id}: {e}'
                            )
                        )
                        logger.error(f'Ошибка отправки напоминания для платежа {payment.payment_id}: {e}')

                if not dry_run:
                    logger.info(f'Отправлено {count} напоминаний о просроченных платежах')
            else:
                self.stdout.write('Нет просроченных платежей для напоминаний')

            if not dry_run:
                self.stdout.write(self.style.SUCCESS('Отправка напоминаний завершена'))
            else:
                self.stdout.write(self.style.SUCCESS('Предварительный просмотр завершен'))

        except Exception as e:
            error_msg = f'Ошибка отправки напоминаний: {e}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)
            raise

    def send_reminder(self, payment):
        """Отправляет напоминание о незавершенном платеже"""
        user = payment.user

        # Определяем текст напоминания
        sub_names = {
            'month': 'Месячная подписка',
            '3months': 'Подписка на 3 месяца',
            '6months': 'Подписка на 6 месяцев',
            'year': 'Годовая подписка'
        }
        sub_name = sub_names.get(payment.subscription_type, 'Подписка')

        # Вычисляем время создания
        time_diff = timezone.now() - payment.created_at
        hours = int(time_diff.total_seconds() // 3600)
        minutes = int((time_diff.total_seconds() % 3600) // 60)

        if hours > 0:
            time_str = f"{hours}ч {minutes}мин назад"
        else:
            time_str = f"{minutes} мин назад"

        message = f"""
⏰ <b>Напоминание о незавершенном платеже</b>

💳 <b>Сумма:</b> {payment.amount} ₽
📅 <b>Подписка:</b> {sub_name}
⏱️ <b>Создан:</b> {time_str}

❌ <b>Ваш платеж все еще ожидает оплаты!</b>

💡 <b>Что делать:</b>
• Создайте новый платеж, если возникли проблемы

🔄 <b>Хотите создать новый платеж?</b>
"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Создать новый платеж", "callback_data": f"retry_payment:{payment.subscription_type}"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        # Отправляем сообщение через Telegram Bot API
        bot_token = getattr(settings, 'BOT_TOKEN', None)
        if not bot_token:
            raise ValueError("BOT_TOKEN не найден в настройках Django")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            'chat_id': user.user_id,
            'text': message,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        }

        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logger.info(f'Отправлено напоминание о незавершенном платеже пользователю {user.user_id}')
            else:
                error_desc = result.get('description', 'Неизвестная ошибка')
                raise Exception(f'Telegram API error: {error_desc}')
        else:
            raise Exception(f'HTTP error: {response.status_code} - {response.text}')
