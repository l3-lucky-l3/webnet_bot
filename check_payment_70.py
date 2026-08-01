#!/usr/bin/env python3
"""
Проверка платежа 70
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
    
    from bot_management.models import Payment, TelegramUser, BalanceTransaction
    
    # Проверяем платеж 70
    try:
        payment = Payment.objects.get(payment_id=70)
        print(f"Платеж 70:")
        print(f"  ID: {payment.payment_id}")
        print(f"  YooKassa ID: {payment.yookassa_payment_id}")
        print(f"  Статус: {payment.status}")
        print(f"  Тип: {payment.subscription_type}")
        print(f"  Сумма: {payment.amount}")
        print(f"  Пользователь: {payment.user.user_id}")
        print(f"  Создан: {payment.created_at}")
        print(f"  Оплачен: {payment.paid_at}")
        
        # Проверяем баланс пользователя
        user = payment.user
        print(f"\nПользователь {user.user_id}:")
        print(f"  Имя: {user.first_name}")
        print(f"  Баланс: {user.balance} ₽")
        
        # Проверяем транзакции баланса для этого платежа
        balance_transaction = BalanceTransaction.objects.filter(payment=payment).first()
        if balance_transaction:
            print(f"\nТранзакция пополнения баланса:")
            print(f"  Тип: {balance_transaction.transaction_type}")
            print(f"  Сумма: {balance_transaction.amount} ₽")
            print(f"  Статус: {balance_transaction.status}")
            print(f"  Описание: {balance_transaction.description}")
        else:
            print("\n❌ Транзакция пополнения баланса не найдена")
            
    except Payment.DoesNotExist:
        print("❌ Платеж 70 не найден")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
