"""
Планировщик уведомлений с использованием APScheduler
Заменяет cron задачи для более надежной работы
"""

import logging
import asyncio
import json
import os
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

from bot_management.services import PaymentService
from bot_management.management.commands.send_payment_reminders import Command as PaymentRemindersCommand
from bot_management.management.commands.send_subscription_reminders import Command as SubscriptionRemindersCommand
from bot_management.management.commands.notify_trial_keys_expired import Command as TrialKeysExpiredCommand
from bot_management.management.commands.renew_multimonth_keys import Command as RenewMultimonthKeysCommand
from bot_management.management.commands.notify_manager_renewal_keys import Command as NotifyManagerRenewalKeysCommand

logger = logging.getLogger(__name__)

# Файл для хранения статуса планировщика
# Определяем путь относительно директории скрипта
_script_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = _script_dir  # Сохраняем в той же директории, где находится скрипт

SCHEDULER_STATUS_FILE = os.path.join(PROJECT_DIR, 'scheduler_status.json')

class NotificationScheduler:
    """
    Планировщик уведомлений на базе APScheduler
    """

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._is_running = False

        # Настройки планировщика
        self.jobstores = {
            'default': MemoryJobStore()
        }
        self.executors = {
            'default': AsyncIOExecutor()
        }
        self.job_defaults = {
            'coalesce': True,  # Объединять пропущенные выполнения
            'max_instances': 1,  # Максимум 1 экземпляр задачи
            'misfire_grace_time': 30  # Время на выполнение пропущенных задач (сек)
        }

    def init_scheduler(self) -> AsyncIOScheduler:
        """
        Инициализация планировщика
        """
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler(
                jobstores=self.jobstores,
                executors=self.executors,
                job_defaults=self.job_defaults,
                timezone='Europe/Moscow'  # Часовой пояс для cron выражений
            )

        return self.scheduler

    async def start_scheduler(self):
        """
        Запуск планировщика
        """
        if self._is_running:
            logger.warning("Планировщик уже запущен")
            return

        try:
            scheduler = self.init_scheduler()

            # Добавляем задачи
            await self._add_notification_jobs(scheduler)

            # Запускаем планировщик
            scheduler.start()
            self._is_running = True

            logger.info("🔔 Планировщик уведомлений запущен")
            logger.info(f"📅 Активных задач: {len(scheduler.get_jobs())}")

            # Сохраняем статус в файл
            self.get_scheduler_status()

        except Exception as e:
            logger.error(f"❌ Ошибка запуска планировщика: {e}")
            raise

    async def stop_scheduler(self):
        """
        Остановка планировщика
        """
        if not self._is_running:
            logger.warning("Планировщик уже остановлен")
            return

        try:
            if self.scheduler:
                self.scheduler.shutdown(wait=True)
                self._is_running = False
                logger.info("🔔 Планировщик уведомлений остановлен")

            # Очищаем файл статуса
            if os.path.exists(SCHEDULER_STATUS_FILE):
                try:
                    os.remove(SCHEDULER_STATUS_FILE)
                    logger.info("🗑 Файл статуса планировщика удален")
                except Exception as e:
                    logger.error(f"Ошибка удаления файла статуса: {e}")

        except Exception as e:
            logger.error(f"❌ Ошибка остановки планировщика: {e}")

    async def _add_notification_jobs(self, scheduler: AsyncIOScheduler):
        """
        Добавление задач уведомлений
        """
        # 1. Напоминания о платежах - каждые 30 минут
        scheduler.add_job(
            func=self._send_payment_reminders,
            trigger=CronTrigger(minute="*/30"),
            id='payment_reminders',
            name='Напоминания о платежах',
            replace_existing=True
        )

        # 2. Напоминания о подписках - каждый час
        scheduler.add_job(
            func=self._send_subscription_reminders,
            trigger=CronTrigger(minute=0),
            id='subscription_reminders',
            name='Напоминания о подписках',
            replace_existing=True
        )

        # 3. Уведомления о закончившихся trial ключах - каждый час
        scheduler.add_job(
            func=self._notify_trial_keys_expired,
            trigger=CronTrigger(minute=0),
            id='trial_keys_expired',
            name='Уведомления о закончившихся trial ключах',
            replace_existing=True
        )

        # 4. Продление ключей для 3м/год подписок - раз в день в 03:00
        scheduler.add_job(
            func=self._renew_multimonth_keys,
            trigger=CronTrigger(hour=3, minute=0),
            id='renew_multimonth_keys',
            name='Продление месячных ключей для 3м/год',
            replace_existing=True
        )

        # 5. Уведомление менеджеру за день до продлений - раз в день в 10:00
        scheduler.add_job(
            func=self._notify_manager_renewal_keys,
            trigger=CronTrigger(hour=10, minute=0),
            id='notify_manager_renewal_keys',
            name='Уведомление менеджеру: ключи на продление завтра',
            replace_existing=True
        )

        logger.info("✅ Задачи уведомлений добавлены в планировщик")

    async def _send_payment_reminders(self):
        """
        Отправка напоминаний о платежах
        """
        try:
            logger.info("💳 Запуск напоминаний о платежах")

            # Создаем экземпляр команды и выполняем
            command = PaymentRemindersCommand()
            await self._run_django_command_async(command)

            logger.info("✅ Напоминания о платежах отправлены")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки напоминаний о платежах: {e}")

    async def _send_subscription_reminders(self):
        """
        Отправка напоминаний о подписках
        """
        try:
            logger.info("📅 Запуск напоминаний о подписках")

            # Создаем экземпляр команды и выполняем
            command = SubscriptionRemindersCommand()
            await self._run_django_command_async(command)

            logger.info("✅ Напоминания о подписках отправлены")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки напоминаний о подписках: {e}")

    async def _notify_trial_keys_expired(self):
        """
        Отправка уведомлений о закончившихся trial ключах
        """
        try:
            logger.info("🎁 Запуск уведомлений о закончившихся trial ключах")

            # Создаем экземпляр команды и выполняем
            command = TrialKeysExpiredCommand()
            await self._run_django_command_async(command)

            logger.info("✅ Уведомления о trial ключах отправлены")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомлений о trial ключах: {e}")

    async def _renew_multimonth_keys(self):
        """Продление месячных ключей для подписок 3м/год по истечении месяца."""
        try:
            logger.info("🔄 Запуск продления ключей для 3м/год подписок")
            command = RenewMultimonthKeysCommand()
            await self._run_django_command_async(command)
            logger.info("✅ Продление ключей завершено")
        except Exception as e:
            logger.error(f"❌ Ошибка продления ключей: {e}")

    async def _notify_manager_renewal_keys(self):
        """Уведомление менеджеру: сколько ключей добавить завтра на продление."""
        try:
            logger.info("📌 Запуск уведомления менеджеру о ключах на продление")
            command = NotifyManagerRenewalKeysCommand()
            await self._run_django_command_async(command)
            logger.info("✅ Уведомление менеджеру отправлено")
        except Exception as e:
            logger.error(f"❌ Ошибка уведомления менеджеру: {e}")

    async def _run_django_command_async(self, command):
        """
        Выполнение Django команды в асинхронном контексте
        """
        # Имитируем options для dry-run=False (реальная отправка)
        options = {
            'dry_run': False,
            'reset_flags': False,
            'verbosity': 1
        }

        # Добавляем специфические опции для каждой команды
        if hasattr(command, 'add_arguments'):
            # Для send_payment_reminders
            if 'send_payment_reminders' in str(type(command)).lower():
                options.update({
                    'max_days': 3,
                    'minutes': 5
                })
            # Для send_subscription_reminders
            elif 'send_subscription_reminders' in str(type(command)).lower():
                options.update({
                    'days_before_expiry': 2,
                    'days_before_expiry_1d': 1,
                    'hours_before_expiry_5h': 5,
                    'hours_before_expiry_1h': 1,
                    'expired_days': 1,
                    'just_expired_hours': 1,  # Уведомлять о подписках, закончившихся ~1 час назад
                    'just_expired_tolerance_hours': 2  # Окно ±2 часа (0-3 часа назад)
                })
            # Для check_keys_availability
            elif 'check_keys_availability' in str(type(command)).lower():
                options.update({
                    'threshold': 2
                })
            # Для notify_trial_keys_expired
            elif 'notify_trial_keys_expired' in str(type(command)).lower():
                options.update({
                    'hours_after_expiry': 24,
                    'tolerance_hours': 12
                })
            # Для renew_multimonth_keys — без доп. опций
            # Для notify_manager_renewal_keys
            elif 'notify_manager_renewal_keys' in str(type(command)).lower():
                options.update({
                    'hours_ahead': 24  # Уведомлять за 24 часа
                })

        # Выполняем команду через handle() метод
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: command.handle(**options)
        )

    def get_scheduler_status(self) -> Dict[str, Any]:
        """
        Получение статуса планировщика
        """
        if not self.scheduler:
            return {"status": "not_initialized"}

        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            })

        status = {
            "status": "running" if self._is_running else "stopped",
            "jobs_count": len(jobs),
            "jobs": jobs,
            "last_update": datetime.now().isoformat()
        }

        # Сохраняем статус в файл для доступа из API
        self._save_status_to_file(status)

        return status

    def _save_status_to_file(self, status: Dict[str, Any]):
        """
        Сохранение статуса в файл
        """
        try:
            logger.info(f"Сохранение статуса в файл: {SCHEDULER_STATUS_FILE}")
            with open(SCHEDULER_STATUS_FILE, 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ Статус сохранен в файл: {SCHEDULER_STATUS_FILE}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения статуса в файл {SCHEDULER_STATUS_FILE}: {e}")
            logger.error(f"Путь к файлу: {SCHEDULER_STATUS_FILE}")
            logger.error(f"Содержимое статуса: {status}")

    @staticmethod
    def load_status_from_file() -> Dict[str, Any]:
        """
        Загрузка статуса из файла
        """
        # Проверяем несколько возможных путей к файлу статуса
        script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            SCHEDULER_STATUS_FILE,  # Основной путь (в директории скрипта)
            os.path.join(script_dir, 'scheduler_status.json'),  # В директории notification_scheduler.py
            os.path.join(os.getcwd(), 'scheduler_status.json'),  # В текущей рабочей директории
            os.path.join(os.path.dirname(script_dir), 'scheduler_status.json'),  # В родительской директории
            '/root/scheduler_status.json',  # Абсолютный путь на сервере
            '/root/123/vpn night bot/vpn night bot1/scheduler_status.json',  # Путь на сервере
        ]

        logger.info(f"Поиск файла статуса в {len(possible_paths)} местах...")

        for file_path in possible_paths:
            try:
                if os.path.exists(file_path):
                    logger.info(f"✅ Найден файл статуса: {file_path}")
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    logger.info(f"✅ Загружен статус: {data.get('status', 'unknown')}")
                    return data
            except Exception as e:
                logger.warning(f"❌ Ошибка чтения файла {file_path}: {e}")
                continue

        logger.warning("Файл статуса не найден ни в одном из возможных мест")
        return {"status": "unknown", "jobs_count": 0, "jobs": []}

    async def add_custom_job(self, job_id: str, func, trigger, **kwargs):
        """
        Добавление кастомной задачи
        """
        if not self.scheduler:
            raise RuntimeError("Планировщик не инициализирован")

        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs
        )
        logger.info(f"✅ Добавлена кастомная задача: {job_id}")

    async def remove_job(self, job_id: str):
        """
        Удаление задачи
        """
        if not self.scheduler:
            return

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"🗑 Удалена задача: {job_id}")
        else:
            logger.warning(f"Задача {job_id} не найдена")

# Глобальный экземпляр планировщика
notification_scheduler = NotificationScheduler()

# Функции для удобного использования
async def start_notification_scheduler():
    """Запуск планировщика уведомлений"""
    await notification_scheduler.start_scheduler()

async def stop_notification_scheduler():
    """Остановка планировщика уведомлений"""
    await notification_scheduler.stop_scheduler()

def get_scheduler_status():
    """Получение статуса планировщика"""
    # Сначала пробуем получить статус от активного планировщика
    if notification_scheduler._is_running and notification_scheduler.scheduler:
        return notification_scheduler.get_scheduler_status()
    else:
        # Если планировщик не активен, пробуем загрузить из файла
        return NotificationScheduler.load_status_from_file()
