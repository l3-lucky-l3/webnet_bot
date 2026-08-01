from django.core.management.base import BaseCommand
from bot_management.models import SupportChat, SupportMessage
from bot_management.services import SupportService


class Command(BaseCommand):
    help = 'Отправляет сообщение в чат поддержки через веб-интерфейс'

    def add_arguments(self, parser):
        parser.add_argument('chat_id', type=int, help='ID чата')
        parser.add_argument('message', type=str, help='Текст сообщения')

    def handle(self, *args, **options):
        chat_id = options['chat_id']
        message = options['message']
        
        try:
            chat = SupportChat.objects.get(chat_id=chat_id)
            
            if chat.status != 'open':
                self.stdout.write(
                    self.style.ERROR(f'Чат {chat_id} закрыт')
                )
                return
            
            # Создаем сообщение в базе
            SupportMessage.objects.create(
                chat=chat,
                sender='admin',
                text=message
            )
            
            # Отправляем сообщение пользователю через бота
            service = SupportService()
            success = service.send_message_to_user_sync(chat_id, message)
            
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Сообщение отправлено в чат {chat_id}')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f'❌ Ошибка отправки сообщения в чат {chat_id}')
                )
                
        except SupportChat.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'Чат {chat_id} не найден')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Ошибка: {e}')
            )
