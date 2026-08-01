"""
Клиент для FGN Connection API - управление VPN ключами.

Эндпоинты:
- /anti/info - информация по ключу
- /anti/create - создание VPN ключа
- /anti/renew - продление ключа (1 мес)
- /anti/reset - сброс трафика на ключе
- /anti/trial - триал обход ключ
- /anti/revoke - перевыпуск ключа
"""

import logging
from typing import Optional

import requests

from config import FGN_CONNECTION_BASE_URL, FGN_CONNECTION_TOKEN

logger = logging.getLogger(__name__)


class FgnConnectionAPIError(Exception):
    """Ошибка при работе с FGN Connection API."""
    def __init__(self, message: str, response_data: Optional[dict] = None):
        self.message = message
        self.response_data = response_data
        super().__init__(self.message)


class FgnConnectionAPI:
    """Клиент для FGN Connection API."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or FGN_CONNECTION_BASE_URL).rstrip('/')
        self.token = token or FGN_CONNECTION_TOKEN
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "token": self.token,
        })

    def _make_request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Выполняет HTTP-запрос к API."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, timeout=30, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error {e.response.status_code}: {e.response.text}"
            logger.error(f"FGN Connection API error: {error_msg}")
            try:
                error_data = e.response.json()
            except Exception:
                error_data = None
            raise FgnConnectionAPIError(error_msg, error_data)
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(f"FGN Connection API error: {error_msg}")
            raise FgnConnectionAPIError(error_msg)
        except ValueError as e:
            error_msg = f"JSON decode error: {str(e)}"
            logger.error(f"FGN Connection API error: {error_msg}")
            raise FgnConnectionAPIError(error_msg)

    def get_key_info(self, key_id: str) -> dict:
        """
        Получить информацию по ключу.
        
        GET /anti/info?key_id=<key_id>
        """
        return self._make_request("GET", "/anti/info", params={"key_id": key_id})

    def create_key(self, tg_id: int, months: int = 1) -> dict:
        """
        Создать новый VPN ключ.
        
        POST /anti/create
        Body: {"tg_id": int, "months": int}
        
        Returns: dict with key data
        """
        return self._make_request("POST", "/anti/create", json={
            "tg_id": tg_id,
            "months": months,
        })

    def renew_key(self, key_id: str) -> dict:
        """
        Продлить ключ на 1 месяц.

        POST /anti/renew?key_id=<key_id>
        """
        return self._make_request("POST", "/anti/renew", params={"key_id": key_id})

    def renew_key_for_months(self, key_id: str, months: int) -> list:
        """
        Продлить ключ на указанное количество месяцев.
        /anti/renew продлевает на 1 месяц, поэтому вызываем его N раз.

        Args:
            key_id: ID ключа
            months: На сколько месяцев продлить

        Returns:
            list: Список ответов от API для каждого вызова
        """
        responses = []
        for i in range(months):
            resp = self.renew_key(key_id)
            responses.append(resp)
            logger.info(f"Продление {i+1}/{months} для ключа {key_id}: {resp}")
        return responses

    def reset_traffic(self, key_id: str) -> dict:
        """
        Сбросить трафик на ключе.
        
        POST /anti/reset?key_id=<key_id>
        """
        return self._make_request("POST", "/anti/reset", params={"key_id": key_id})

    def create_trial(self, tg_id: int) -> dict:
        """
        Создать триал ключ с обходом ограничений.
        
        POST /anti/trial?tg_id=<tg_id>
        """
        return self._make_request("POST", "/anti/trial", params={"tg_id": tg_id})

    def revoke_key(self, key_id: str) -> dict:
        """
        Перевыпустить ключ (revoke).
        
        POST /anti/revoke?key_id=<key_id>
        """
        return self._make_request("POST", "/anti/revoke", params={"key_id": key_id})


# Singleton instance
_api_instance: Optional[FgnConnectionAPI] = None


def get_api() -> FgnConnectionAPI:
    """Получить singleton экземпляр API клиента."""
    global _api_instance
    if _api_instance is None:
        _api_instance = FgnConnectionAPI()
    return _api_instance
