#!/usr/bin/env python3
"""
Скрипт для создания тестовых промокодов
"""

import os
import sys
import django

# Настройка Django
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tg_bot_admin.settings')

try:
    django.setup()
    from bot_management.promo_models import PromoCode
    from django.utils import timezone
    from datetime import timedelta
    
    print("✅ Django настроен успешно")
    
    # Создаем тестовые промокоды
    test_promos = [
        {
            'code': 'WELCOME10',
            'description': 'Скидка 10% для новых пользователей',
            'discount_type': 'percent',
            'discount_value': 10,
            'min_amount': 100,
            'max_uses': 1000,
            'valid_until': timezone.now() + timedelta(days=30)
        },
        {
            'code': 'NEWUSER',
            'description': 'Фиксированная скидка 100₽ для новых пользователей',
            'discount_type': 'fixed',
            'discount_value': 100,
            'min_amount': 200,
            'max_uses': 500,
            'valid_until': timezone.now() + timedelta(days=60)
        },
        {
            'code': 'VIP20',
            'description': 'Скидка 20% для VIP пользователей',
            'discount_type': 'percent',
            'discount_value': 20,
            'min_amount': 500,
            'max_uses': 100,
            'valid_until': timezone.now() + timedelta(days=90)
        },
        {
            'code': 'SUMMER50',
            'description': 'Летняя скидка 50₽',
            'discount_type': 'fixed',
            'discount_value': 50,
            'min_amount': 100,
            'max_uses': 200,
            'valid_until': timezone.now() + timedelta(days=15)
        }
    ]
    
    created_count = 0
    for promo_data in test_promos:
        try:
            promo, created = PromoCode.objects.get_or_create(
                code=promo_data['code'],
                defaults=promo_data
            )
            if created:
                print(f"✅ Создан промокод: {promo.code} - {promo.description}")
                created_count += 1
            else:
                print(f"⚠️ Промокод уже существует: {promo.code}")
        except Exception as e:
            print(f"❌ Ошибка создания промокода {promo_data['code']}: {e}")
    
    print(f"\n🎉 Создано промокодов: {created_count}")
    print(f"📊 Всего промокодов в базе: {PromoCode.objects.count()}")
    
except Exception as e:
    print(f"❌ Ошибка настройки Django: {e}")
    sys.exit(1)
