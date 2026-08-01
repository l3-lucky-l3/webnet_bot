#!/usr/bin/env python
"""
Скрипт для отправки сообщений поддержки через веб-интерфейс
Использование: python send_support_web.py
"""

import webbrowser
import time
import subprocess
import os

def open_support_page():
    """Открывает страницу отправки сообщений поддержки"""
    url = "http://thoughtfully-active-manakin.cloudpub.ru/bot_management/send-support/"
    webbrowser.open(url)
    print(f"🌐 Открыта страница: {url}")
    print("📝 Введите ID чата и сообщение на странице")

if __name__ == '__main__':
    open_support_page()
