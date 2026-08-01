"""
Поиск доступных эндпоинтов Remnawave API
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

async def check_endpoint(session, method, endpoint):
    """Проверка одного эндпоинта"""
    url = f"{REMNAWAVE_BASE_URL.rstrip('/')}{endpoint}"
    try:
        async with session.request(method, url, headers=HEADERS) as response:
            result = await response.json()
            return {
                'endpoint': endpoint,
                'method': method,
                'status': response.status,
                'result': result
            }
    except Exception as e:
        return {
            'endpoint': endpoint,
            'method': method,
            'status': 'error',
            'result': str(e)
        }

async def explore_api():
    """Исследование доступных эндпоинтов API"""
    print("=" * 80)
    print("Исследование доступных эндпоинтов Remnawave API")
    print("=" * 80)
    
    # Список возможных эндпоинтов
    endpoints_to_check = [
        # Основные
        ('GET', '/api/users'),
        ('GET', '/api/v1/users'),
        
        # Squads
        ('GET', '/api/squads'),
        ('GET', '/api/v1/squads'),
        ('GET', '/api/internal-squads'),
        ('GET', '/api/v1/internal-squads'),
        
        # Servers
        ('GET', '/api/servers'),
        ('GET', '/api/v1/servers'),
        
        # Settings
        ('GET', '/api/settings'),
        ('GET', '/api/v1/settings'),
        ('GET', '/api/config'),
        ('GET', '/api/v1/config'),
        
        # Info
        ('GET', '/api/info'),
        ('GET', '/api/v1/info'),
        ('GET', '/api/health'),
        ('GET', '/api/v1/health'),
        
        # Nodes
        ('GET', '/api/nodes'),
        ('GET', '/api/v1/nodes'),
    ]
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for method, endpoint in endpoints_to_check:
            tasks.append(check_endpoint(session, method, endpoint))
        
        results = await asyncio.gather(*tasks)
        
        print("\n")
        for result in results:
            status = result['status']
            endpoint = result['endpoint']
            method = result['method']
            
            # Показываем только успешные или интересные ответы
            if status == 200:
                print(f"✅ {method} {endpoint}")
                result_data = result.get('result', {})
                if isinstance(result_data, dict):
                    keys = list(result_data.keys())[:5]
                    print(f"   Keys: {keys}")
                print()
            elif status == 404:
                print(f"❌ {method} {endpoint} - 404 Not Found")
            else:
                print(f"⚠️ {method} {endpoint} - {status}")
                print(f"   Response: {str(result.get('result', {}))[:100]}")
                print()

if __name__ == '__main__':
    asyncio.run(explore_api())
