from django.apps import AppConfig


class BotManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bot_management'

    def ready(self):
        # Уведомления о trial-ключах и другие задачи сейчас реализованы через APScheduler
        # (см. notification_scheduler.py и команды management/commands/*.py).
        # Альтернативный вариант с Django signals отключен, чтобы избежать дублирования логики.
        # import bot_management.signals
        pass