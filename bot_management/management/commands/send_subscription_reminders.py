from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bot_management.models import Payment, TelegramUser
import logging
import requests
import json
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Отправляет напоминания о заканчивающихся подписках'

    def handle_error_and_cleanup(self, error, payment, user_id):
        """
        Обрабатывает ошибки отправки и удаляет данные при необходимости
        """
        error_str = str(error).lower()

        # Ошибки, при которых пользователь заблокировал бота или деактивирован
        cleanup_errors = [
            'forbidden: bot was blocked by the user',
            'bad request: user is deactivated',
            'bad request: chat not found',
            'forbidden: user is deactivated',
            'bad request: chat_id is invalid'
        ]

        should_cleanup = any(cleanup_error in error_str for cleanup_error in cleanup_errors)

        if should_cleanup:
            try:
                # Удаляем все платежи пользователя
                deleted_payments = Payment.objects.filter(user__user_id=user_id).delete()
                # Можно также деактивировать пользователя
                user = payment.user
                user.is_active = False
                user.save()

                self.stdout.write(
                    self.style.WARNING(
                        f'🗑️ Пользователь {user_id} заблокировал бота. Удалено платежей: {deleted_payments[0]}, пользователь деактивирован'
                    )
                )
                logger.warning(f'Пользователь {user_id} заблокировал бота. Очищены данные: платежи={deleted_payments[0]}')

            except Exception as cleanup_error:
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ Ошибка при очистке данных пользователя {user_id}: {cleanup_error}'
                    )
                )
                logger.error(f'Ошибка при очистке данных пользователя {user_id}: {cleanup_error}')
        else:
            # Обычная ошибка - логируем, но не удаляем
            self.stdout.write(
                self.style.ERROR(
                    f'❌ Ошибка отправки уведомления для платежа {payment.payment_id}: {error}'
                )
            )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать что будет отправлено без фактической отправки'
        )
        parser.add_argument(
            '--days-before-expiry',
            type=int,
            default=2,
            help='Дни до окончания подписки для отправки напоминания (по умолчанию: 2)'
        )
        parser.add_argument(
            '--days-before-expiry-1d',
            type=int,
            default=1,
            help='Дни до окончания подписки для отправки напоминания за 1 день (по умолчанию: 1)'
        )
        parser.add_argument(
            '--hours-before-expiry-5h',
            type=int,
            default=5,
            help='Часов до окончания подписки для отправки напоминания за 5 часов (по умолчанию: 5)'
        )
        parser.add_argument(
            '--hours-before-expiry-1h',
            type=int,
            default=1,
            help='Часов до окончания подписки для отправки напоминания за 1 час (по умолчанию: 1)'
        )
        parser.add_argument(
            '--expired-days',
            type=int,
            default=1,
            help='Дни после окончания подписки для отправки уведомления (по умолчанию: 1)'
        )
        parser.add_argument(
            '--just-expired-hours',
            type=int,
            default=0,
            help='Часы после окончания подписки для уведомления "только что закончилась" (по умолчанию: 0 - сразу)'
        )
        parser.add_argument(
            '--just-expired-tolerance-hours',
            type=int,
            default=1,
            help='Окно погрешности для уведомлений о только что закончившихся подписках (по умолчанию: 1 час)'
        )
        parser.add_argument(
            '--reset-flags',
            action='store_true',
            help='Сбросить флаги отправленных напоминаний для повторной отправки'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days_before = options['days_before_expiry']
        days_before_1d = options['days_before_expiry_1d']
        hours_before_5h = options.get('hours_before_expiry_5h', 5)
        hours_before_1h = options.get('hours_before_expiry_1h', 1)
        expired_days = options['expired_days']
        just_expired_hours = options['just_expired_hours']
        just_expired_tolerance = options['just_expired_tolerance_hours']
        reset_flags = options['reset_flags']

        try:
            # Сброс флагов отправленных напоминаний
            if reset_flags:
                reset_subscription_count = Payment.objects.filter(subscription_reminder_sent=True).update(subscription_reminder_sent=False)
                reset_subscription_1d_count = Payment.objects.filter(subscription_reminder_1d_sent=True).update(subscription_reminder_1d_sent=False)
                reset_subscription_5h_count = Payment.objects.filter(subscription_reminder_5h_sent=True).update(subscription_reminder_5h_sent=False)
                reset_subscription_1h_count = Payment.objects.filter(subscription_reminder_1h_sent=True).update(subscription_reminder_1h_sent=False)
                reset_expiry_count = Payment.objects.filter(expiry_reminder_sent=True).update(expiry_reminder_sent=False)
                reset_just_expired_count = Payment.objects.filter(subscription_just_expired_notified=True).update(subscription_just_expired_notified=False)
                reset_trial_count = Payment.objects.filter(trial_reminder_sent=True).update(trial_reminder_sent=False)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Сброшены флаги: подписки 2д {reset_subscription_count}, подписки 1д {reset_subscription_1d_count}, '
                        f'подписки 5ч {reset_subscription_5h_count}, подписки 1ч {reset_subscription_1h_count}, '
                        f'только что закончились {reset_just_expired_count}, '
                        f'истечение {reset_expiry_count}, пробные {reset_trial_count}'
                    )
                )
                return

            if dry_run:
                self.stdout.write(self.style.WARNING('=== РЕЖИМ ПРЕДВАРИТЕЛЬНОГО ПРОСМОТРА ==='))

            # Находим подписки, которые только что закончились
            if just_expired_hours >= 0:
                self.send_just_expired_reminders(just_expired_hours, just_expired_tolerance, dry_run)

            # Находим подписки, которые заканчиваются через N дней (2 дня)
            self.send_expiring_soon_reminders(days_before, dry_run, reminder_field='subscription_reminder_sent')

            # Находим подписки, которые заканчиваются через 1 день
            self.send_expiring_soon_reminders(days_before_1d, dry_run, reminder_field='subscription_reminder_1d_sent')

            # Находим подписки, которые заканчиваются через N часов (5 часов)
            self.send_expiring_hour_reminders(hours_before_5h, dry_run, reminder_field='subscription_reminder_5h_sent')

            # Находим подписки, которые заканчиваются через 1 час
            self.send_expiring_hour_reminders(hours_before_1h, dry_run, reminder_field='subscription_reminder_1h_sent')

            # Находим просроченные подписки
            self.send_expired_reminders(expired_days, dry_run)

            if not dry_run:
                self.stdout.write(self.style.SUCCESS('Отправка напоминаний о подписках завершена'))
            else:
                self.stdout.write(self.style.SUCCESS('Предварительный просмотр завершен'))

        except Exception as e:
            error_msg = f'Ошибка отправки напоминаний о подписках: {e}'
            self.stdout.write(self.style.ERROR(error_msg))
            logger.error(error_msg)
            raise

    def send_expiring_soon_reminders(self, days_before, dry_run=False, reminder_field='subscription_reminder_sent'):
        """Отправляет напоминания о подписках, которые скоро закончатся"""
        try:
            # Рассчитываем дату окончания (через N дней от сейчас)
            expiry_date = timezone.now() + timedelta(days=days_before)
            next_day = expiry_date + timedelta(days=1)

            # Находим платежи с подпиской, которая заканчивается в указанный период
            # Исключаем платежи, по которым уже отправлялись напоминания
            filters = {
                'status': 'succeeded',
                'subscription_expires_at__gte': expiry_date,
                'subscription_expires_at__lt': next_day,
                reminder_field: False
            }
            expiring_payments = Payment.objects.filter(
                **filters
            ).exclude(
                subscription_expires_at__isnull=True
            ).select_related('user')

            count = expiring_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} подписок, заканчивающихся через {days_before} дней')

                for payment in expiring_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                f'Будет отправлено напоминание пользователю {payment.user.user_id} '
                                f'о подписке {payment.subscription_type}, заканчивается {payment.subscription_expires_at}'
                            )
                        else:
                            self.send_expiring_reminder(payment, days_before)
                            # Помечаем, что напоминание о заканчивающейся подписке отправлено
                            setattr(payment, reminder_field, True)
                            payment.save(update_fields=[reminder_field])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено напоминание пользователю {payment.user.user_id} '
                                    f'о подписке {payment.subscription_type} ({days_before} дн)'
                                )
                            )
                    except Exception as e:
                        self.handle_error_and_cleanup(e, payment, payment.user.user_id)
                        logger.error(f'Ошибка отправки напоминания для платежа {payment.payment_id}: {e}')
            else:
                self.stdout.write(f'Нет подписок, заканчивающихся через {days_before} дней')

        except Exception as e:
            logger.error(f'Ошибка отправки напоминаний о заканчивающихся подписках: {e}')
            raise

    def send_expiring_hour_reminders(self, hours_before, dry_run=False, reminder_field='subscription_reminder_1h_sent'):
        """Отправляет напоминания о подписках, которые скоро закончатся (с точностью до часа)"""
        try:
            # Рассчитываем время окончания (через N часов от сейчас)
            expiry_time = timezone.now() + timedelta(hours=hours_before)
            next_hour = expiry_time + timedelta(hours=1)

            # Находим платежи с подпиской, которая заканчивается в указанный период
            filters = {
                'status': 'succeeded',
                'subscription_expires_at__gte': expiry_time,
                'subscription_expires_at__lt': next_hour,
                reminder_field: False
            }
            expiring_payments = Payment.objects.filter(
                **filters
            ).exclude(
                subscription_expires_at__isnull=True
            ).select_related('user')

            count = expiring_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} подписок, заканчивающихся через {hours_before} часов')

                for payment in expiring_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                f'Будет отправлено напоминание пользователю {payment.user.user_id} '
                                f'о подписке {payment.subscription_type}, заканчивается {payment.subscription_expires_at}'
                            )
                        else:
                            self.send_hourly_expiring_reminder(payment, hours_before)
                            setattr(payment, reminder_field, True)
                            payment.save(update_fields=[reminder_field])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено напоминание пользователю {payment.user.user_id} '
                                    f'о подписке {payment.subscription_type} ({hours_before} ч)'
                                )
                            )
                    except Exception as e:
                        self.handle_error_and_cleanup(e, payment, payment.user.user_id)
                        logger.error(f'Ошибка отправки напоминания для платежа {payment.payment_id}: {e}')
            else:
                self.stdout.write(f'Нет подписок, заканчивающихся через {hours_before} часов')

        except Exception as e:
            logger.error(f'Ошибка отправки напоминаний о заканчивающихся подписках (часы): {e}')
            raise

    def send_hourly_expiring_reminder(self, payment, hours_left):
        """Отправляет напоминание о заканчивающейся подписке (с часами до окончания)"""
        user = payment.user

        sub_names = {
            'trial': 'Пробная подписка',
            'month': 'Месячная подписка (30 дней)',
            '3months': 'Подписка на 3 месяца (90 дней)',
            '6months': 'Подписка на 6 месяцев (180 дней)',
            'year': 'Годовая подписка (365 дней)'
        }
        sub_name = sub_names.get(payment.subscription_type, f'Подписка ({payment.subscription_type})')

        expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y %H:%M') if payment.subscription_expires_at else 'неизвестно'

        message = f"""
⚠️ <b>Ваша подписка скоро закончится!</b>

📅 <b>Тип подписки:</b> {sub_name}
⏰ <b>Осталось часов:</b> {hours_left}
📆 <b>Дата окончания:</b> {expiry_date}

💡 <b>Что делать:</b>
• Продлите подписку заранее, чтобы оставаться на связи

🔄 <b>Продлить подписку:</b>
"""

        keyboard = {
            "inline_keyboard": [
                [{"text": "💳 Продлить подписку", "callback_data": "catalog"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        self._send_telegram_message(user.user_id, message, keyboard)

    def send_expired_reminders(self, expired_days, dry_run=False):
        """Отправляет уведомления о просроченных подписках"""
        try:
            # Рассчитываем дату окончания (N дней назад)
            expiry_cutoff = timezone.now() - timedelta(days=expired_days)

            # Находим платежи с истекшей подпиской
            # Исключаем платежи, по которым уже отправлялись уведомления об истечении
            expired_payments = Payment.objects.filter(
                status='succeeded',
                subscription_expires_at__lte=expiry_cutoff,
                expiry_reminder_sent=False  # Только платежи без отправленных уведомлений об истечении
            ).exclude(
                subscription_expires_at__isnull=True
            ).select_related('user')

            # Отдельно обрабатываем истекшие пробные подписки
            self.send_trial_expired_reminders(expired_days, dry_run)

            count = expired_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} просроченных подписок (старше {expired_days} дней)')

                for payment in expired_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                f'Будет отправлено уведомление пользователю {payment.user.user_id} '
                                f'о просроченной подписке {payment.subscription_type}, истекла {payment.subscription_expires_at}'
                            )
                        else:
                            self.send_expired_reminder(payment)
                            # Помечаем, что уведомление об истечении подписки отправлено
                            payment.expiry_reminder_sent = True
                            payment.save(update_fields=['expiry_reminder_sent'])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено уведомление пользователю {payment.user.user_id} '
                                    f'о просроченной подписке {payment.subscription_type}'
                                )
                            )
                    except Exception as e:
                        self.handle_error_and_cleanup(e, payment, payment.user.user_id)
                        logger.error(f'Ошибка отправки уведомления для просроченного платежа {payment.payment_id}: {e}')
            else:
                self.stdout.write(f'Нет просроченных подписок старше {expired_days} дней')

        except Exception as e:
            logger.error(f'Ошибка отправки уведомлений о просроченных подписках: {e}')
            raise

    def send_expiring_reminder(self, payment, days_left):
        """Отправляет напоминание о заканчивающейся подписке"""
        user = payment.user

        # Определяем текст напоминания
        sub_names = {
            'trial': 'Пробная подписка (3 дня)',
            'month': 'Месячная подписка (30 дней)',
            '3months': 'Подписка на 3 месяца (90 дней)',
            '6months': 'Подписка на 6 месяцев (180 дней)',
            'year': 'Годовая подписка (365 дней)'
        }
        sub_name = sub_names.get(payment.subscription_type, f'Подписка ({payment.subscription_type})')

        expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else 'неизвестно'

        message = f"""
⚠️ <b>Ваша подписка скоро закончится!</b>

📅 <b>Тип подписки:</b> {sub_name}
⏰ <b>Осталось дней:</b> {days_left}
📆 <b>Дата окончания:</b> {expiry_date}

💡 <b>Что делать:</b>
• Продлите подписку заранее, чтобы оставаться на связи

🔄 <b>Продлить подписку:</b>
"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "💳 Продлить подписку", "callback_data": "catalog"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        self._send_telegram_message(user.user_id, message, keyboard)

    def send_expired_reminder(self, payment):
        """Отправляет уведомление о просроченной подписке"""
        user = payment.user

        # Определяем текст уведомления
        sub_names = {
            'trial': 'Пробная подписка (3 дня)',
            'month': 'Месячная подписка (30 дней)',
            '3months': 'Подписка на 3 месяца (90 дней)',
            '6months': 'Подписка на 6 месяцев (180 дней)',
            'year': 'Годовая подписка (365 дней)'
        }
        sub_name = sub_names.get(payment.subscription_type, f'Подписка ({payment.subscription_type})')

        expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else 'неизвестно'

        message = f"""
❌ <b>Ваша подписка закончилась!</b>

📅 <b>Тип подписки:</b> {sub_name}
📆 <b>Дата окончания:</b> {expiry_date}

⚠️ <b>Доступ к VPN ограничен</b>

💡 <b>Что делать:</b>
• Оплатите подписку для восстановления доступа
• Получите новый VPN ключ

🔄 <b>Возобновить подписку:</b>
"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Возобновить подписку", "callback_data": "catalog"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        self._send_telegram_message(user.user_id, message, keyboard)

    def send_trial_expired_reminders(self, expired_days, dry_run=False):
        """Отправляет уведомления об окончании пробного периода"""
        try:
            # Рассчитываем дату окончания (N дней назад)
            expiry_cutoff = timezone.now() - timedelta(days=expired_days)

            # Находим платежи с истекшим пробным периодом
            trial_expired_payments = Payment.objects.filter(
                status='succeeded',
                subscription_type='trial',
                subscription_expires_at__lte=expiry_cutoff,
                trial_reminder_sent=False  # Только платежи без отправленных уведомлений
            ).exclude(
                subscription_expires_at__isnull=True
            ).select_related('user')

            count = trial_expired_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} истекших пробных подписок (старше {expired_days} дней)')

                for payment in trial_expired_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                f'Будет отправлено уведомление пользователю {payment.user.user_id} '
                                f'об окончании пробного периода, истек {payment.subscription_expires_at}'
                            )
                        else:
                            self.send_trial_expired_reminder(payment)
                            # Помечаем, что уведомление об окончании пробного периода отправлено
                            payment.trial_reminder_sent = True
                            payment.save(update_fields=['trial_reminder_sent'])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено уведомление пользователю {payment.user.user_id} '
                                    f'об окончании пробного периода'
                                )
                            )
                    except Exception as e:
                        self.handle_error_and_cleanup(e, payment, payment.user.user_id)
                        logger.error(f'Ошибка отправки уведомления для пробного платежа {payment.payment_id}: {e}')
            else:
                self.stdout.write(f'Нет истекших пробных подписок старше {expired_days} дней')

        except Exception as e:
            logger.error(f'Ошибка отправки уведомлений об окончании пробного периода: {e}')
            raise

    def send_just_expired_reminders(self, just_expired_hours, tolerance_hours, dry_run=False):
        """Отправляет уведомления о только что закончившихся подписках"""
        try:
            now = timezone.now()

            # Вычисляем временное окно для поиска подписок, которые только что закончились
            min_expired_time = now - timedelta(hours=just_expired_hours + tolerance_hours)
            max_expired_time = now - timedelta(hours=just_expired_hours - tolerance_hours)

            self.stdout.write(
                f'Ищем подписки, закончившиеся между {min_expired_time} и {max_expired_time}'
            )

            # Находим платежи, которые только что закончились
            just_expired_payments = Payment.objects.filter(
                status='succeeded',
                subscription_expires_at__range=(min_expired_time, max_expired_time),
                subscription_just_expired_notified=False,  # Уведомление еще не отправлено
                subscription_type__in=['trial', 'month', '3months', '6months', 'year']  # Включая trial подписки
            ).exclude(
                subscription_expires_at__isnull=True
            ).select_related('user').order_by('subscription_expires_at')

            count = just_expired_payments.count()

            if count > 0:
                self.stdout.write(f'Найдено {count} подписок, которые только что закончились')

                for payment in just_expired_payments:
                    try:
                        if dry_run:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Будет отправлено уведомление пользователю {payment.user.user_id} '
                                    f'о закончившейся подписке {payment.subscription_type}, '
                                    f'истекла {payment.subscription_expires_at}'
                                )
                            )
                        else:
                            self.send_just_expired_reminder(payment)
                            # Помечаем, что уведомление отправлено
                            payment.subscription_just_expired_notified = True
                            payment.save(update_fields=['subscription_just_expired_notified'])
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Отправлено уведомление пользователю {payment.user.user_id} '
                                    f'о закончившейся подписке'
                                )
                            )
                    except Exception as e:
                        self.handle_error_and_cleanup(e, payment, payment.user.user_id)
                        logger.error(f'Ошибка отправки уведомления для платежа {payment.payment_id}: {e}')
            else:
                self.stdout.write('Нет подписок, которые только что закончились')

        except Exception as e:
            logger.error(f'Ошибка отправки уведомлений о только что закончившихся подписках: {e}')
            raise

    def send_just_expired_reminder(self, payment):
        """Отправляет уведомление о только что закончившейся подписке"""
        user = payment.user

        # Определяем текст уведомления с длительностью
        sub_names = {
            'trial': 'Пробная подписка (3 дня)',
            'month': 'Месячная подписка (30 дней)',
            '3months': 'Подписка на 3 месяца (90 дней)',
            '6months': 'Подписка на 6 месяцев (180 дней)',
            'year': 'Годовая подписка (365 дней)'
        }
        sub_name = sub_names.get(payment.subscription_type, f'Подписка ({payment.subscription_type})')

        expiry_date = payment.subscription_expires_at.strftime('%d.%m.%Y') if payment.subscription_expires_at else 'неизвестно'

        message = f"""
❌ <b>Ваша подписка только что закончилась!</b>

📅 <b>Тип подписки:</b> {sub_name}
📆 <b>Дата окончания:</b> {expiry_date}

⚠️ <b>Доступ к VPN может быть ограничен</b>

💡 <b>Что делать:</b>
• Продлите подписку для восстановления доступа
• Получите новый VPN ключ

🔄 <b>Возобновить подписку:</b>
"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Возобновить подписку", "callback_data": "catalog"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        self._send_telegram_message(user.user_id, message, keyboard)

    def send_trial_expired_reminder(self, payment):
        """Отправляет уведомление об окончании пробного периода"""
        user = payment.user

        message = """⚠️<b>ВНИМАНИЕ!</b> ⚠️

Ваш пробный период VPN с обходом блокировок закончился.

Без подписки доступ будет закрыт.

✨ Хотите продлить подписку?"""

        # Создаем клавиатуру с кнопками
        keyboard = {
            "inline_keyboard": [
                [{"text": "💳 Купить подписку", "callback_data": "catalog"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]
            ]
        }

        self._send_telegram_message(user.user_id, message, keyboard)

    def _send_telegram_message(self, user_id, message, keyboard=None):
        """Отправляет сообщение через Telegram Bot API"""
        bot_token = getattr(settings, 'BOT_TOKEN', None)
        if not bot_token:
            raise ValueError("BOT_TOKEN не найден в настройках Django")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            'chat_id': user_id,
            'text': message,
            'parse_mode': 'HTML'
        }

        if keyboard:
            data['reply_markup'] = json.dumps(keyboard)

        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logger.info(f'Отправлено уведомление о подписке пользователю {user_id}')
            else:
                error_desc = result.get('description', 'Неизвестная ошибка')
                raise Exception(f'Telegram API error: {error_desc}')
        else:
            raise Exception(f'HTTP error: {response.status_code} - {response.text}')


