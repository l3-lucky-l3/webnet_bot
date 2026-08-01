#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для обновления базы данных
Добавляет колонку photo_file_id в таблицу support_messages
"""

import sqlite3
import os

DB_NAME = "tg_key_bot.db"

def update_database():
    """Обновляет базу данных, добавляя колонку photo_file_id"""
    
    if not os.path.exists(DB_NAME):
        print(f"❌ База данных {DB_NAME} не найдена")
        return False
    
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        print("🔧 Обновляем базу данных...")
        
        # Проверяем, существует ли колонка photo_file_id
        cursor.execute("PRAGMA table_info(support_messages)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'photo_file_id' in columns:
            print("✅ Колонка photo_file_id уже существует")
        else:
            # Добавляем колонку photo_file_id
            cursor.execute("ALTER TABLE support_messages ADD COLUMN photo_file_id TEXT")
            print("✅ Колонка photo_file_id добавлена")
        
        # Проверяем, существует ли колонка is_read
        if 'is_read' in columns:
            print("✅ Колонка is_read уже существует")
        else:
            # Добавляем колонку is_read
            cursor.execute("ALTER TABLE support_messages ADD COLUMN is_read BOOLEAN DEFAULT 0")
            print("✅ Колонка is_read добавлена")
        
        conn.commit()
        print("✅ База данных успешно обновлена!")
        
        # Показываем структуру таблицы
        cursor.execute("PRAGMA table_info(support_messages)")
        columns = cursor.fetchall()
        
        print("\n📋 Структура таблицы support_messages:")
        for column in columns:
            print(f"  - {column[1]} ({column[2]})")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка обновления базы данных: {e}")
        return False
    
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    update_database()








