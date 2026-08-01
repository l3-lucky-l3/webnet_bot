#!/usr/bin/env python
"""
Скрипт для настройки автоматической очистки системы
"""

import os
import sys
import platform
import subprocess
from pathlib import Path

def setup_linux_cron():
    """Настройка cron для Linux/Unix"""
    print("🐧 Настройка автоматической очистки для Linux/Unix...")
    
    # Получаем путь к проекту
    project_dir = Path.cwd()
    python_path = sys.executable
    
    print(f"Путь к проекту: {project_dir}")
    print(f"Путь к Python: {python_path}")
    
    # Создаем cron задачу
    cron_job = f"0 2 * * * cd {project_dir} && {python_path} manage.py daily_cleanup >> /var/log/django_cleanup.log 2>&1"
    
    try:
        # Получаем текущие cron задачи
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        current_crontab = result.stdout if result.returncode == 0 else ""
        
        # Проверяем, есть ли уже наша задача
        if "daily_cleanup" in current_crontab:
            print("✅ Задача уже существует в crontab")
        else:
            # Добавляем новую задачу
            new_crontab = current_crontab + f"\n{cron_job}\n"
            subprocess.run(['crontab', '-'], input=new_crontab, text=True, check=True)
            print("✅ Cron задача добавлена")
        
        print(f"Задача: {cron_job}")
        
        # Создаем лог файл
        try:
            subprocess.run(['sudo', 'touch', '/var/log/django_cleanup.log'], check=True)
            subprocess.run(['sudo', 'chmod', '666', '/var/log/django_cleanup.log'], check=True)
            print("✅ Лог файл создан: /var/log/django_cleanup.log")
        except subprocess.CalledProcessError:
            print("⚠️  Не удалось создать лог файл (требуются права sudo)")
        
        print("✅ Настройка завершена!")
        print("Для проверки логов: tail -f /var/log/django_cleanup.log")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка настройки cron: {e}")
        return False
    
    return True

def setup_windows_task():
    """Настройка задачи для Windows"""
    print("🪟 Настройка автоматической очистки для Windows...")
    
    project_dir = Path.cwd()
    python_path = sys.executable
    
    print(f"Путь к проекту: {project_dir}")
    print(f"Путь к Python: {python_path}")
    
    # Создаем команду для задачи
    command = f'cmd /c cd /d "{project_dir}" && "{python_path}" manage.py daily_cleanup >> cleanup.log 2>&1'
    
    try:
        # Создаем задачу в планировщике Windows
        subprocess.run([
            'schtasks', '/create',
            '/tn', 'Django Daily Cleanup',
            '/tr', command,
            '/sc', 'daily',
            '/st', '02:00',
            '/f'
        ], check=True)
        
        print("✅ Задача создана: Django Daily Cleanup")
        print("Время выполнения: ежедневно в 2:00")
        print("Лог файл: cleanup.log")
        
        # Показываем созданную задачу
        result = subprocess.run([
            'schtasks', '/query', '/tn', 'Django Daily Cleanup', '/fo', 'list'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("Детали задачи:")
            print(result.stdout)
        
        print("✅ Настройка завершена!")
        print("Для проверки логов: type cleanup.log")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка создания задачи: {e}")
        return False
    
    return True

def main():
    """Основная функция"""
    print("🚀 Настройка автоматической очистки системы")
    print("=" * 50)
    
    # Определяем операционную систему
    system = platform.system().lower()
    
    if system == "linux" or system == "darwin" or "unix" in system:
        success = setup_linux_cron()
    elif system == "windows":
        success = setup_windows_task()
    else:
        print(f"❌ Неподдерживаемая операционная система: {system}")
        print("Настройте автоматическую очистку вручную:")
        print("1. Создайте cron задачу или задачу планировщика")
        print("2. Запускайте: python manage.py daily_cleanup")
        print("3. Время: ежедневно в 2:00")
        return
    
    if success:
        print("\n🎉 Автоматическая очистка настроена!")
        print("\nДоступные команды:")
        print("  python manage.py system_status     - Проверка статуса")
        print("  python manage.py daily_cleanup     - Ручная очистка")
        print("  python manage.py daily_cleanup --dry-run - Предварительный просмотр")
    else:
        print("\n❌ Ошибка настройки автоматической очистки")
        print("Настройте вручную согласно документации")

if __name__ == "__main__":
    main()
