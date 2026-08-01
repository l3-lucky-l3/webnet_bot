#!/usr/bin/env python
import os
import sys
import django

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
django.setup()

from bot_management.models import TelegramUser, Referral, ReferralReward
from bot_management.referral_models import Referral as ReferralModel

def check_referrals():
    print("=== ПРОВЕРКА РЕФЕРАЛЬНОЙ СИСТЕМЫ ===\n")
    
    # Проверяем пользователей
    print("1. Пользователи:")
    users = TelegramUser.objects.all()
    for user in users:
        print(f"   ID: {user.user_id}, Username: @{user.username}, Баланс: {user.balance} ₽")
    
    print(f"\nВсего пользователей: {users.count()}")
    
    # Проверяем реферальные связи
    print("\n2. Реферальные связи:")
    try:
        referrals = ReferralModel.objects.all()
        for ref in referrals:
            print(f"   {ref.referrer.user_id} -> {ref.referred.user_id} (активна: {ref.is_active})")
        print(f"\nВсего реферальных связей: {referrals.count()}")
    except Exception as e:
        print(f"   Ошибка: {e}")
    
    # Проверяем награды
    print("\n3. Реферальные награды:")
    try:
        rewards = ReferralReward.objects.all()
        for reward in rewards:
            print(f"   ID: {reward.id}, Сумма: {reward.reward_value} ₽, Статус: {reward.status}")
        print(f"\nВсего наград: {rewards.count()}")
    except Exception as e:
        print(f"   Ошибка: {e}")
    
    # Проверяем конкретного пользователя
    print("\n4. Проверка пользователя 6484952272:")
    try:
        user = TelegramUser.objects.get(user_id=6484952272)
        print(f"   Пользователь найден: @{user.username}")
        print(f"   Баланс: {user.balance} ₽")
        print(f"   Способ входа: {user.first_entry_method}")
        
        # Проверяем, есть ли реферальная связь
        try:
            referral = ReferralModel.objects.get(referred=user)
            print(f"   Приглашен пользователем: {referral.referrer.user_id}")
            print(f"   Связь активна: {referral.is_active}")
        except ReferralModel.DoesNotExist:
            print("   НЕ приглашен по реферальной ссылке")
            
    except TelegramUser.DoesNotExist:
        print("   Пользователь не найден")

if __name__ == "__main__":
    check_referrals()








