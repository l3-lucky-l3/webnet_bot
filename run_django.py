#!/usr/bin/env python
"""
Скрипт для запуска Django сервера
"""
import os
import sys
import django
from django.core.management import execute_from_command_line

if __name__ == "__main__":
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
    django.setup()
    execute_from_command_line(sys.argv)
