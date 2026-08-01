#!/usr/bin/env python3
"""
Скрипт для замены жестко прописанных Django URL на переменную DJANGO_API_URL
"""

import re

FILE_PATH = 'bot_with_django.py'

# Читаем файл
with open(FILE_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# Заменяем все вхождения http://127.0.0.1:8123 на {DJANGO_API_URL}
old_url = "http://127.0.0.1:8123"
new_pattern = "{DJANGO_API_URL}"

# Используем regex для замены
pattern = r"http://127\.0\.0\.1:8123"
replacement = "{DJANGO_API_URL}"

content = re.sub(pattern, replacement, content)

# Записываем обратно
with open(FILE_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"✅ Заменено {content.count('{DJANGO_API_URL}')} вхождений URL")
print("Теперь нужно добавить форматирование строк в местах использования")
