#!/usr/bin/env python3
"""
Проверка данных платежа
"""

import os
import sys
import django
from django.conf import settings

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')

try:
    django.setup()
    print("✅ Django настроен успешно")
    
    from bot_management.models import Payment
    
    # Проверяем платеж 69
    payment = Payment.objects.get(payment_id=69)
    print(f"Платеж 69:")
    print(f"  ID: {payment.payment_id}")
    print(f"  YooKassa ID: {payment.yookassa_payment_id}")
    print(f"  Статус: {payment.status}")
    print(f"  Тип: {payment.subscription_type}")
    print(f"  Сумма: {payment.amount}")
    print(f"  Пользователь: {payment.user.user_id}")
    
    # Ищем платеж по YooKassa ID
    yookassa_id = "3080f365-000f-5000-b000-14c8c256ffb8"
    try:
        payment_by_yookassa = Payment.objects.get(yookassa_payment_id=yookassa_id)
        print(f"\n✅ Платеж найден по YooKassa ID: {payment_by_yookassa.payment_id}")
    except Payment.DoesNotExist:
        print(f"\n❌ Платеж с YooKassa ID {yookassa_id} не найден")
        
        # Показываем все платежи с YooKassa ID
        print("\nВсе платежи с YooKassa ID:")
        for p in Payment.objects.filter(yookassa_payment_id__isnull=False):
            print(f"  ID: {p.payment_id}, YooKassa: {p.yookassa_payment_id}")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
