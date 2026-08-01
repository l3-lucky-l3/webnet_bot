#!/usr/bin/env python
"""
Скрипт для отправки сообщений поддержки
Использование: python send_support_message.py <chat_id> <message>
"""

import os
import sys
import django

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
django.setup()

from bot_management.models import SupportChat, SupportMessage
from bot_management.services import SupportService


def send_support_message(chat_id, message):
    """Отправляет сообщение в чат поддержки"""
    try:
        chat = SupportChat.objects.get(chat_id=chat_id)
        
        if chat.status != 'open':
            print(f'❌ Чат {chat_id} закрыт')
            return False
        
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
            print(f'✅ Сообщение отправлено в чат {chat_id}')
            return True
        else:
            print(f'❌ Ошибка отправки сообщения в чат {chat_id}')
            return False
            
    except SupportChat.DoesNotExist:
        print(f'❌ Чат {chat_id} не найден')
        return False
    except Exception as e:
        print(f'❌ Ошибка: {e}')
        return False


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Использование: python send_support_message.py <chat_id> <message>')
        sys.exit(1)
    
    chat_id = int(sys.argv[1])
    message = sys.argv[2]
    
    send_support_message(chat_id, message)
