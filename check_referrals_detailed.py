#!/usr/bin/env python
import os
import sys
import django

# Добавляем путь к проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')
django.setup()

from bot_management.models import TelegramUser
from bot_management.referral_models import Referral as ReferralModel

def check_detailed():
    print("=== ДЕТАЛЬНАЯ ПРОВЕРКА РЕФЕРАЛЬНЫХ СВЯЗЕЙ ===\n")
    
    # Проверяем все реферальные связи
    referrals = ReferralModel.objects.select_related('referrer', 'referred').all()
    
    for ref in referrals:
        print(f"Реферальная связь ID {ref.id}:")
        print(f"  Реферер: {ref.referrer.user_id} (@{ref.referrer.username})")
        print(f"  Приглашенный: {ref.referred.user_id} (@{ref.referred.username})")
        print(f"  Активна: {ref.is_active}")
        print(f"  Способ входа реферера: {ref.referrer.first_entry_method}")
        print(f"  Способ входа приглашенного: {ref.referred.first_entry_method}")
        print()
    
    # Проверяем, кто кого может пригласить
    print("=== КТО МОЖЕТ ПОЛУЧИТЬ НАГРАДУ ===")
    
    # Если spasibotobi (5191853594) покупает что-то, то pavelsurron (6484952272) должен получить награду
    print("Если spasibotobi (5191853594) покупает - pavelsurron (6484952272) получает награду")
    
    # Если pavelsurron (6484952272) покупает что-то, то никто не получает награду (он сам реферер)
    print("Если pavelsurron (6484952272) покупает - никто не получает награду")

if __name__ == "__main__":
    check_detailed()








