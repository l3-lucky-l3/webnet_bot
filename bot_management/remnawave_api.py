"""
Remnawave API Client для интеграции с обычным VPN
Документация Remnawave API: https://docs.remnawave.com/
"""

import aiohttp
import logging
from typing import Optional, Dict, Any
from django.conf import settings

logger = logging.getLogger(__name__)


class RemnawaveAPI:
    """Клиент для работы с Remnawave API"""

    def __init__(self, base_url: str, api_key: str, default_squad_uuid=None):
        """
        Инициализация клиента Remnawave API

        Args:
            base_url: Базовый URL Remnawave панели (например, https://webnetspace.fun)
            api_key: API ключ для авторизации (JWT токен)
            default_squad_uuid: UUID конфигурации по умолчанию. Может быть строкой или списком строк.
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        if isinstance(default_squad_uuid, list):
            self.default_squad_uuid = default_squad_uuid
        elif default_squad_uuid:
            self.default_squad_uuid = [default_squad_uuid]
        else:
            self.default_squad_uuid = None
        # Remnawave использует Authorization: Bearer <token>
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
    
    async def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Выполнить HTTP запрос к Remnawave API
        
        Args:
            method: HTTP метод (GET, POST, PUT, DELETE)
            endpoint: Endpoint относительно base_url
            data: Данные для отправки (для POST/PUT)
            
        Returns:
            Dict с ответом API
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with aiohttp.ClientSession() as session:
                logger.info(f"Remnawave API {method} {url}")
                if data:
                    logger.info(f"Remnawave API request body: {data}")
                async with session.request(
                    method,
                    url,
                    headers=self.headers,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    try:
                        result = await response.json()
                    except Exception:
                        text = await response.text()
                        logger.error(f"Remnawave API non-JSON response: {text}")
                        result = {'raw': text}
                    
                    logger.info(f"Remnawave API response status: {response.status}")
                    logger.info(f"Remnawave API response body: {result}")
                    
                    if response.status not in [200, 201]:
                        error_detail = result.get('detail', result.get('message', str(result)))
                        logger.error(f"Remnawave API error: {response.status} - {error_detail}")
                        raise RemnawaveAPIError(f"API error: {response.status} - {error_detail}", result)
                    
                    return result
        except RemnawaveAPIError:
            raise
        except aiohttp.ClientError as e:
            logger.error(f"Remnawave API connection error: {e}")
            raise RemnawaveAPIError(f"Connection error: {e}")
        except Exception as e:
            logger.error(f"Remnawave API unexpected error: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise RemnawaveAPIError(f"Unexpected error: {e}")
    
    async def get_user(self, telegram_id: int) -> Optional[Dict]:
        """
        Получить пользователя по Telegram ID
        
        Args:
            telegram_id: Telegram ID пользователя
            
        Returns:
            Список пользователей (Remnawave возвращает массив) или None
        """
        try:
            # Remnawave возвращает список пользователей с таким telegram_id
            result = await self._request('GET', f'/api/users/by-telegram-id/{telegram_id}')
            response = result.get('response', [])
            # Возвращаем первого пользователя или None
            return response[0] if response else None
        except RemnawaveAPIError:
            return None

    async def get_user_by_uuid(self, user_uuid: str) -> Optional[Dict]:
        """
        Получить пользователя по UUID
        
        Args:
            user_uuid: UUID пользователя в Remnawave
            
        Returns:
            Dict с данными пользователя или None
        """
        try:
            result = await self._request('GET', f'/api/users/{user_uuid}')
            response = result.get('response')
            return response
        except RemnawaveAPIError:
            return None
    
    async def create_user(self, telegram_id: int, username: Optional[str] = None, expire_days: int = 365) -> Dict:
        """
        Создать нового пользователя в Remnawave
        """
        import uuid as uuid_mod
        from datetime import datetime, timedelta, timezone

        # Генерируем валидный username: 3-36 символов, только [a-zA-Z0-9_-]
        if username:
            # Очищаем от недопустимых символов
            clean_name = ''.join(c for c in username if c.isalnum() or c in '_-')
            clean_name = clean_name[:30]  # Оставляем место для суффикса
            if len(clean_name) < 3:
                clean_name = f"user{clean_name}"
            generated_username = f"{clean_name}_{uuid_mod.uuid4().hex[:6]}"
        else:
            generated_username = f"user{telegram_id}_{uuid_mod.uuid4().hex[:6]}"
        
        # Гарантируем длину 3-36
        generated_username = generated_username[:36]
        if len(generated_username) < 3:
            generated_username = f"usr{generated_username}"

        # Форматируем expireAt с миллисекундами: 2025-01-17T15:38:45.065Z
        expire_dt = datetime.now(timezone.utc) + timedelta(days=expire_days)
        expire_at = expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{expire_dt.microsecond // 1000:03d}Z"

        data = {
            'username': generated_username,
            'expireAt': expire_at,
            'status': 'ACTIVE',
            'trafficLimitBytes': 0,
            'trafficLimitStrategy': 'NO_RESET',
            'description': f'Telegram ID: {telegram_id}',
            'hwidDeviceLimit': 3,
        }

        if self.default_squad_uuid:
            data['activeInternalSquads'] = self.default_squad_uuid

        logger.info(f"Remnawave create_user: username={generated_username}, expireAt={expire_at}, squad={self.default_squad_uuid}")

        result = await self._request('POST', '/api/users', data)
        return result.get('response', {})
    
    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None, expire_days: int = 365) -> Dict:
        """
        Получить или создать пользователя
        
        Args:
            telegram_id: Telegram ID пользователя
            username: Username пользователя (уникальный для каждого платежа)
            expire_days: Срок действия аккаунта в днях
            
        Returns:
            Dict с данными пользователя
        """
        # Сначала ищем по telegram_id
        user = await self.get_user(telegram_id)
        if user:
            # Пользователь найден - обновляем его срок действия
            logger.info(f"Пользователь найден по telegram_id={telegram_id}, uuid={user.get('uuid')}")
            return user
        
        # Не найден - создаем нового
        logger.info(f"Пользователь не найден, создаем нового telegram_id={telegram_id}, username={username}")
        return await self.create_user(telegram_id, username, expire_days)
    
    async def create_subscription(self, telegram_id: int, plan_id: int, duration_days: int) -> Dict:
        """
        Создать подписку для пользователя
        
        Args:
            telegram_id: Telegram ID пользователя
            plan_id: ID тарифного плана в Remnawave
            duration_days: Длительность подписки в днях
            
        Returns:
            Dict с данными подписки
        """
        data = {
            'telegram_id': telegram_id,
            'plan_id': plan_id,
            'duration_days': duration_days
        }
        result = await self._request('POST', '/api/subscriptions', data)
        return result.get('subscription', {})
    
    async def create_subscription_key(self, telegram_id: int, subscription_type: str) -> Dict:
        """
        Создать/продлить подписку для пользователя и вернуть subscription URL
        
        Args:
            telegram_id: Telegram ID пользователя
            subscription_type: Тип подписки ('day', 'month', '3months', '6months', 'year', '2years')
            
        Returns:
            Dict с subscription URL и данными пользователя
        """
        from datetime import datetime, timedelta
        
        # Маппинг типов подписок на длительность в днях
        duration_map = {
            'day': 1,
            'month': 30,
            '3months': 90,
            '6months': 180,
            'year': 365,
            '2years': 730,
            'week': 7,
        }
        
        duration_days = duration_map.get(subscription_type, 30)
        
        # Получаем или создаем пользователя
        user = await self.get_or_create_user(telegram_id, expire_days=duration_days)
        user_uuid = user.get('uuid')
        
        if not user_uuid:
            raise RemnawaveAPIError("Не удалось получить UUID пользователя в Remnawave")
        
        # Продлеваем подписку пользователя (обновляем expireAt)
        new_expire_at = (datetime.utcnow() + timedelta(days=duration_days)).isoformat() + 'Z'
        
        update_data = {
            'uuid': user_uuid,
            'expireAt': new_expire_at,
            'status': 'ACTIVE'
        }
        
        updated_user = await self._request('PATCH', '/api/users', update_data)
        updated_user = updated_user.get('response', {})
        
        # Возвращаем subscription URL и данные
        return {
            'subscription_key': updated_user.get('subscriptionUrl'),
            'subscription_url': updated_user.get('subscriptionUrl'),
            'uuid': user_uuid,
            'username': updated_user.get('username'),
            'expireAt': new_expire_at,
            'id': updated_user.get('id'),
            'subscription_id': user_uuid
        }
    
    async def get_user_keys(self, telegram_id: int) -> list:
        """
        Получить подписку пользователя (subscription URL)
        
        Args:
            telegram_id: Telegram ID пользователя
            
        Returns:
            List с subscription URL пользователя
        """
        try:
            users = await self._request('GET', f'/api/users/by-telegram-id/{telegram_id}')
            users_list = users.get('response', [])
            
            result = []
            for user in users_list:
                if user.get('subscriptionUrl'):
                    result.append({
                        'subscription_url': user.get('subscriptionUrl'),
                        'username': user.get('username'),
                        'status': user.get('status'),
                        'expireAt': user.get('expireAt'),
                        'uuid': user.get('uuid')
                    })
            
            return result
        except RemnawaveAPIError:
            return []
    
    async def get_user_subscription(self, telegram_id: int) -> Optional[Dict]:
        """
        Получить активную подписку пользователя
        
        Args:
            telegram_id: Telegram ID пользователя
            
        Returns:
            Dict с данными подписки или None
        """
        try:
            result = await self._request('GET', f'/api/users/telegram/{telegram_id}/subscription')
            return result.get('subscription')
        except RemnawaveAPIError:
            return None
    
    async def extend_subscription(self, telegram_id: int, duration_days: int) -> Dict:
        """
        Продлить подписку пользователя

        Args:
            telegram_id: Telegram ID пользователя
            duration_days: На сколько дней продлить

        Returns:
            Dict с данными обновленной подписки
        """
        from datetime import datetime, timedelta, timezone as dt_timezone

        user = await self.get_user(telegram_id)
        if not user:
            raise RemnawaveAPIError("User not found")

        user_uuid = user.get('uuid')

        current_expire = user.get('expireAt')
        if current_expire:
            try:
                current_expire_dt = datetime.fromisoformat(current_expire.replace('Z', '+00:00'))
                new_expire_dt = current_expire_dt + timedelta(days=duration_days)
            except:
                new_expire_dt = datetime.now(dt_timezone.utc) + timedelta(days=duration_days)
        else:
            new_expire_dt = datetime.now(dt_timezone.utc) + timedelta(days=duration_days)

        new_expire_at = new_expire_dt.strftime('%Y-%m-%dT%H:%M:%S.') + f"{new_expire_dt.microsecond // 1000:03d}Z"

        update_data = {
            'uuid': user_uuid,
            'expireAt': new_expire_at,
            'status': 'ACTIVE'
        }
        
        # Добавляем конфигурацию (squad) если указана и у пользователя её нет
        # Примечание: Remnawave API использует activeInternalSquads для указания squad
        if self.default_squad_uuid and not user.get('externalSquadUuid'):
            update_data['activeInternalSquads'] = self.default_squad_uuid
        
        result = await self._request('PATCH', '/api/users', update_data)
        return result.get('response', {})
    
    async def get_user_devices(self, user_uuid: str) -> Optional[list]:
        """
        Получить список HWID устройств пользователя.

        Args:
            user_uuid: UUID пользователя в Remnawave

        Returns:
            list устройств или None
        """
        try:
            result = await self._request('GET', f'/api/hwid/devices/{user_uuid}')
            return result.get('response', {}).get('devices', [])
        except RemnawaveAPIError:
            return None

    async def delete_user_device(self, user_uuid: str, hwid: str) -> bool:
        """
        Удалить конкретное HWID устройство пользователя.

        Args:
            user_uuid: UUID пользователя в Remnawave
            hwid: HWID устройства

        Returns:
            True если успешно
        """
        try:
            await self._request('POST', '/api/hwid/devices/delete', {
                'userUuid': user_uuid,
                'hwid': hwid,
            })
            return True
        except RemnawaveAPIError:
            return False

    async def delete_all_user_devices(self, user_uuid: str) -> bool:
        """
        Удалить все HWID устройства пользователя.

        Args:
            user_uuid: UUID пользователя в Remnawave

        Returns:
            True если успешно
        """
        try:
            await self._request('POST', '/api/hwid/devices/delete-all', {
                'userUuid': user_uuid,
            })
            return True
        except RemnawaveAPIError:
            return False

    async def revoke_key(self, key_id: int) -> bool:
        """
        Отозвать ключ доступа
        
        Args:
            key_id: ID ключа в Remnawave
            
        Returns:
            True если успешно
        """
        try:
            await self._request('DELETE', f'/api/keys/{key_id}')
            return True
        except RemnawaveAPIError:
            return False
    
    async def get_server_stats(self) -> Dict:
        """
        Получить статистику сервера
        
        Returns:
            Dict со статистикой
        """
        # Remnawave может не иметь этого эндпоинта, опционально
        try:
            return await self._request('GET', '/api/v1/stats')
        except RemnawaveAPIError:
            # Пытаемся альтернативный эндпоинт
            try:
                return await self._request('GET', '/api/stats')
            except RemnawaveAPIError:
                return {'status': 'ok', 'message': 'Stats endpoint not available'}


class RemnawaveAPIError(Exception):
    """Исключение для ошибок Remnawave API"""
    
    def __init__(self, message: str, response_data: Optional[Dict] = None):
        self.message = message
        self.response_data = response_data or {}
        super().__init__(self.message)
    
    def __str__(self):
        return f"RemnawaveAPIError: {self.message}"


_remnawave_client: Optional[RemnawaveAPI] = None
_remnawave_bypass_client: Optional[RemnawaveAPI] = None
_remnawave_fast_vpn_client: Optional[RemnawaveAPI] = None


def get_remnawave_client() -> Optional[RemnawaveAPI]:
    """
    Получить глобальный экземпляр Remnawave API клиента (для Обычного VPN)
    """
    global _remnawave_client

    if _remnawave_client is not None:
        return _remnawave_client

    base_url = getattr(settings, 'REMNAWAVE_BASE_URL', None)
    api_key = getattr(settings, 'REMNAWAVE_API_KEY', None)
    default_squad_uuid = getattr(settings, 'REMNAWAVE_DEFAULT_SQUAD_UUID', None)

    if not base_url or not api_key:
        logger.warning("Remnawave API settings not configured")
        return None

    _remnawave_client = RemnawaveAPI(base_url, api_key, default_squad_uuid)
    logger.info(f"Remnawave API client initialized with URL: {base_url}, squad: {default_squad_uuid or 'default'}")

    return _remnawave_client


def init_remnawave_client(base_url: str, api_key: str, default_squad_uuid=None) -> RemnawaveAPI:
    """
    Инициализировать Remnawave API клиент (для Обычного VPN)
    """
    global _remnawave_client
    _remnawave_client = RemnawaveAPI(base_url, api_key, default_squad_uuid)
    logger.info(f"Remnawave API client initialized with URL: {base_url}, squad: {default_squad_uuid or 'default'}")
    return _remnawave_client


def get_remnawave_bypass_client() -> Optional[RemnawaveAPI]:
    """
    Получить глобальный экземпляр Remnawave API клиента для ОБХОДА (Night VPN)
    """
    global _remnawave_bypass_client

    if _remnawave_bypass_client is not None:
        return _remnawave_bypass_client

    base_url = getattr(settings, 'REMNAWAVE_BYPASS_BASE_URL', None)
    api_key = getattr(settings, 'REMNAWAVE_BYPASS_API_KEY', None)
    default_squad_uuid = getattr(settings, 'REMNAWAVE_BYPASS_DEFAULT_SQUAD_UUID', None)

    if not base_url or not api_key:
        logger.warning("Remnawave Bypass API settings not configured")
        return None

    _remnawave_bypass_client = RemnawaveAPI(base_url, api_key, default_squad_uuid)
    logger.info(f"Remnawave Bypass API client initialized with URL: {base_url}, squad: {default_squad_uuid or 'default'}")

    return _remnawave_bypass_client


def init_remnawave_bypass_client(base_url: str, api_key: str, default_squad_uuid=None) -> RemnawaveAPI:
    """
    Инициализировать Remnawave API клиент для ОБХОДА (Night VPN)
    """
    global _remnawave_bypass_client
    _remnawave_bypass_client = RemnawaveAPI(base_url, api_key, default_squad_uuid)
    logger.info(f"Remnawave Bypass API client initialized with URL: {base_url}, squad: {default_squad_uuid or 'default'}")
    return _remnawave_bypass_client


def get_remnawave_fast_vpn_client() -> Optional[RemnawaveAPI]:
    """
    Получить глобальный экземпляр Remnawave API клиента для Обычный VPN (1 squad)
    """
    global _remnawave_fast_vpn_client

    if _remnawave_fast_vpn_client is not None:
        return _remnawave_fast_vpn_client

    base_url = getattr(settings, 'REMNAWAVE_BYPASS_BASE_URL', None)
    api_key = getattr(settings, 'REMNAWAVE_BYPASS_API_KEY', None)
    fast_vpn_squad = getattr(settings, 'REMNAWAVE_FAST_VPN_SQUAD_UUID', None)

    if not base_url or not api_key:
        logger.warning("Remnawave Обычный VPN API settings not configured (needs REMNAWAVE_BYPASS_BASE_URL and REMNAWAVE_BYPASS_API_KEY)")
        return None

    squad_uuid = [fast_vpn_squad] if fast_vpn_squad else None
    _remnawave_fast_vpn_client = RemnawaveAPI(base_url, api_key, squad_uuid)
    logger.info(f"Remnawave Обычный VPN API client initialized with URL: {base_url}, squad: {squad_uuid or 'default'}")

    return _remnawave_fast_vpn_client


def init_remnawave_fast_vpn_client(base_url: str, api_key: str, default_squad_uuid=None) -> RemnawaveAPI:
    """
    Инициализировать Remnawave API клиент для Обычный VPN
    """
    global _remnawave_fast_vpn_client
    _remnawave_fast_vpn_client = RemnawaveAPI(base_url, api_key, default_squad_uuid)
    logger.info(f"Remnawave Обычный VPN API client initialized with URL: {base_url}, squad: {default_squad_uuid or 'default'}")
    return _remnawave_fast_vpn_client
