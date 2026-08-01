"""
Проверка существующих пользователей на наличие привязки к Default-Squad
"""

import asyncio
import os
from dotenv import load_dotenv
import aiohttp

# Загружаем .env
load_dotenv('/home/hacker/qwen1/zerkalo/.env')

REMNAWAVE_BASE_URL = os.getenv('REMNAWAVE_BASE_URL')
REMNAWAVE_API_KEY = os.getenv('REMNAWAVE_API_KEY')

HEADERS = {
    'Authorization': f'Bearer {REMNAWAVE_API_KEY}',
    'Content-Type': 'application/json'
}

async def check_all_users():
    """Проверка всех пользователей"""
    print("=" * 80)
    print("Проверка всех пользователей на привязку к конфигурациям")
    print("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        # Получаем всех пользователей
        async with session.get(
            f"{REMNAWAVE_BASE_URL.rstrip('/')}/api/users",
            headers=HEADERS
        ) as response:
            result = await response.json()
            users_data = result.get('response', {}).get('users', [])
            
        print(f"\nВсего пользователей: {len(users_data)}\n")
        
        # Проверяем каждого пользователя
        for i, user in enumerate(users_data):
            username = user.get('username', 'N/A')
            telegram_id = user.get('telegramId')
            active_squads = user.get('activeInternalSquads', [])
            external_squad = user.get('externalSquadUuid')
            
            squad_names = [squad['name'] for squad in active_squads] if active_squads else []
            
            print(f"{i+1}. {username}")
            print(f"   Telegram ID: {telegram_id}")
            print(f"   Active Squads: {squad_names if squad_names else 'Нет'}")
            print(f"   External Squad: {external_squad if external_squad else 'Нет'}")
            print()

if __name__ == '__main__':
    asyncio.run(check_all_users())
