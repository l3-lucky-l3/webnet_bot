@echo off
echo Настройка автоматической очистки для Windows...

REM Получаем путь к проекту
set PROJECT_DIR=%~dp0
set PYTHON_PATH=python

echo Путь к проекту: %PROJECT_DIR%
echo Путь к Python: %PYTHON_PATH%

REM Создаем задачу в планировщике Windows
schtasks /create /tn "Django Daily Cleanup" /tr "cmd /c cd /d %PROJECT_DIR% && %PYTHON_PATH% manage.py daily_cleanup >> cleanup.log 2>&1" /sc daily /st 02:00 /f

echo Задача создана: Django Daily Cleanup
echo Время выполнения: ежедневно в 2:00
echo Лог файл: cleanup.log

REM Показываем созданную задачу
schtasks /query /tn "Django Daily Cleanup" /fo list

echo Настройка завершена!
echo Для проверки логов используйте: type cleanup.log
