@echo off
echo Очистка закрытых чатов поддержки...
cd /d "E:\tg bots"
python manage.py cleanup_closed_chats
echo Очистка завершена.
