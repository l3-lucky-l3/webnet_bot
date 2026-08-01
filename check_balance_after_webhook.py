#!/usr/bin/env python3
"""
Проверка баланса после webhook'а
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
    
    # Проверяем платеж 69
    payment = Payment.objects.get(payment_id=69)
    print(f"Платеж 69:")
    print(f"  ID: {payment.payment_id}")
    print(f"  Статус: {payment.status}")
    print(f"  Тип: {payment.subscription_type}")
    print(f"  Сумма: {payment.amount}")
    print(f"  Пользователь: {payment.user.user_id}")
    
    # Проверяем баланс пользователя
    user = payment.user
    print(f"\nПользователь {user.user_id}:")
    print(f"  Имя: {user.first_name}")
    print(f"  Баланс: {user.balance} ₽")
    
    # Проверяем транзакции баланса
    transactions = BalanceTransaction.objects.filter(user=user).order_by('-created_at')[:5]
    print(f"\nПоследние транзакции баланса:")
    for t in transactions:
        print(f"  {t.created_at}: {t.transaction_type} - {t.amount} ₽ ({t.status})")
    
    # Проверяем, есть ли транзакция пополнения для этого платежа
    balance_transaction = BalanceTransaction.objects.filter(payment=payment).first()
    if balance_transaction:
        print(f"\nТранзакция пополнения баланса:")
        print(f"  Тип: {balance_transaction.transaction_type}")
        print(f"  Сумма: {balance_transaction.amount} ₽")
        print(f"  Статус: {balance_transaction.status}")
        print(f"  Описание: {balance_transaction.description}")
    else:
        print("\n❌ Транзакция пополнения баланса не найдена")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
