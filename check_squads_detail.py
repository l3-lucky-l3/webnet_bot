"""
Проверка internal-squads и nodes
"""

import asyncio
import os
from dotenv import load_dotenv
import aiohttp
import json

# Загружаем .env
load_dotenv('/home/hacker/qwen1/zerkalo/.env')

REMNAWAVE_BASE_URL = os.getenv('REMNAWAVE_BASE_URL')
REMNAWAVE_API_KEY = os.getenv('REMNAWAVE_API_KEY')

HEADERS = {
    'Authorization': f'Bearer {REMNAWAVE_API_KEY}',
    'Content-Type': 'application/json'
}

async def get_internal_squads():
    """Получение списка internal squads"""
    print("=" * 80)
    print("Internal Squads")
    print("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{REMNAWAVE_BASE_URL.rstrip('/')}/api/internal-squads",
            headers=HEADERS
        ) as response:
            result = await response.json()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            
            squads = result.get('response', [])
            print(f"\n\nВсего squads: {len(squads)}")
            for squad in squads:
                print(f"\n- {squad.get('name')}")
                print(f"  UUID: {squad.get('uuid')}")
                print(f"  ID: {squad.get('id')}")
            
            return squads

async def get_nodes():
    """Получение списка nodes"""
    print("\n\n" + "=" * 80)
    print("Nodes")
    print("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{REMNAWAVE_BASE_URL.rstrip('/')}/api/nodes",
            headers=HEADERS
        ) as response:
            result = await response.json()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            
            nodes = result.get('response', [])
            print(f"\n\nВсего nodes: {len(nodes)}")
            for node in nodes:
                print(f"\n- {node.get('name')}")
                print(f"  UUID: {node.get('uuid')}")
                print(f"  ID: {node.get('id')}")
            
            return nodes

async def test_add_user_to_squad_via_patch(squad_uuid, user_uuid):
    """Попытка добавить пользователя в squad через PATCH"""
    print("\n\n" + "=" * 80)
    print(f"Тест добавления пользователя {user_uuid} в squad {squad_uuid}")
    print("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        # Пробуем обновить пользователя с указанием internalSquadIds
        update_data = {
            'uuid': user_uuid,
            'internalSquadIds': [squad_uuid]
        }
        
        async with session.patch(
            f"{REMNAWAVE_BASE_URL.rstrip('/')}/api/users",
            headers=HEADERS,
            json=update_data
        ) as response:
            result = await response.json()
            print(f"Status: {response.status}")
            print(f"Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
            
            if response.status == 200:
                user = result.get('response', {})
                active_squads = user.get('activeInternalSquads', [])
                print(f"\nActive Squads после обновления: {[s.get('name') for s in active_squads]}")

async def main():
    squads = await get_internal_squads()
    nodes = await get_nodes()
    
    # Если есть squads, пробуем добавить пользователя
    if squads:
        # Создаем тестового пользователя
        import random
        telegram_id = random.randint(1000000000, 9999999999)
        username = f"test_squad_add_{telegram_id}"
        
        create_data = {
            'username': username,
            'expireAt': '2027-03-27T00:00:00.000Z',
            'status': 'ACTIVE',
            'trafficLimitBytes': 0,
            'trafficLimitStrategy': 'NO_RESET',
            'description': f'Telegram ID: {telegram_id}',
            'hwidDeviceLimit': 1
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{REMNAWAVE_BASE_URL.rstrip('/')}/api/users",
                headers=HEADERS,
                json=create_data
            ) as response:
                result = await response.json()
                user = result.get('response', {})
                user_uuid = user.get('uuid')
                print(f"\n\nСоздан тестовый пользователь: {user_uuid}")
        
        # Пробуем добавить в первый squad
        default_squad = squads[0]
        await test_add_user_to_squad_via_patch(default_squad['uuid'], user_uuid)

if __name__ == '__main__':
    asyncio.run(main())
