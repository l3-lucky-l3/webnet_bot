#!/usr/bin/env python3
"""
Диагностика файла статуса планировщика на сервере
Использование: python3 diagnose_scheduler_file.py
"""

import os
import json
import sys

def diagnose_scheduler_file():
    """Диагностика файла статуса планировщика"""
    print("🔍 ДИАГНОСТИКА ФАЙЛА СТАТУСА ПЛАНИРОВЩИКА")
    print("=" * 50)

    # Определяем возможные пути к файлу
    script_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(script_dir, 'scheduler_status.json'),
        os.path.join(os.getcwd(), 'scheduler_status.json'),
        os.path.join(os.path.dirname(script_dir), 'scheduler_status.json'),
        '/root/scheduler_status.json',
        '/root/123/vpn night bot/vpn night bot1/scheduler_status.json',
    ]

    print(f"📂 Текущая директория: {os.getcwd()}")
    print(f"📂 Директория скрипта: {script_dir}")
    print()

    found_files = []

    for i, file_path in enumerate(possible_paths, 1):
        exists = os.path.exists(file_path)
        status = "✅ НАЙДЕН" if exists else "❌ НЕ НАЙДЕН"

        print(f"{i}. {status}: {file_path}")

        if exists:
            found_files.append(file_path)
            try:
                # Проверяем размер файла
                size = os.path.getsize(file_path)
                print(f"   📏 Размер: {size} байт")

                # Проверяем права доступа
                stat = os.stat(file_path)
                print(f"   🔐 Права: {oct(stat.st_mode)[-3:]}")
                print(f"   👤 Владелец: {stat.st_uid}:{stat.st_gid}")

                # Проверяем JSON
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                print("   📊 JSON: корректный")
                print(f"   📈 Статус: {data.get('status', 'unknown')}")
                print(f"   🎯 Задач: {data.get('jobs_count', 0)}")

                if data.get('jobs'):
                    print("   📋 Задачи:")
                    for job in data['jobs'][:3]:  # Показываем первые 3
                        print(f"      • {job.get('name', 'N/A')}")

            except json.JSONDecodeError as e:
                print(f"   ❌ JSON: поврежден - {e}")
            except Exception as e:
                print(f"   ❌ Ошибка чтения: {e}")
        print()

    print("🎯 РЕЗУЛЬТАТ ДИАГНОСТИКИ:")
    if found_files:
        print(f"✅ Найдено {len(found_files)} файл(ов) статуса")
        print("📄 Файлы:", ", ".join(found_files))
    else:
        print("❌ Файлы статуса не найдены!")
        print("💡 Рекомендации:")
        print("   1. Перезапустите бота")
        print("   2. Проверьте логи бота")
        print("   3. Создайте файл вручную если нужно")

    print()
    print("=" * 50)

if __name__ == "__main__":
    diagnose_scheduler_file()
