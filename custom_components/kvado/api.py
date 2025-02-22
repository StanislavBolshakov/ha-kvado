import aiohttp
import asyncio
import logging
from typing import Dict, Optional, Any, List
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.mobile.pc.kvado.ru"
AUTH_ENDPOINT = "/2.0/authentication"
ACCOUNTS_ENDPOINT = "/2.0/profile/accounts"
RECEIPTS_ENDPOINT = "/3.0/receipts"
METERS_ENDPOINT = "/2.0/meters"

DEFAULT_HEADERS = {
    "Kvado-Certificate": "K6zJ74q8pH49MeKZNqe4BZYymgdjCAxdKJTS37umE8s84rLxu7BCYnRY6CDYbNHf",
    "App-Source": "app_lk_citizen",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": "AndroidResidentAccount/1.0",
}

MAX_RETRIES = 5
INITIAL_BACKOFF = 4
BACKOFF_MULTIPLIER = 2


class KvadoApiClient:
    def __init__(
        self,
        hass: Any,
        username: str,
        password: str,
        token: Optional[str] = None,
        session_id: Optional[str] = None,
        entry_id: Optional[str] = None,
    ):
        self.hass = hass
        self.username = username
        self.password = password
        self.token = token
        self.session_id = session_id
        self.entry_id = entry_id

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        headers: Dict,
        payload: Optional[Dict] = None,
    ) -> Optional[Dict]:
        url = f"{BASE_URL}{endpoint}"
        backoff = INITIAL_BACKOFF

        _LOGGER.debug(f"Request: {method} {url}")
        if payload:
            _LOGGER.debug(f"Payload: {payload}")

        for attempt in range(MAX_RETRIES):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method, url, headers=headers, json=payload
                    ) as response:
                        response_text = await response.text()
                        _LOGGER.debug(f"Response {response.status}: {response_text}")

                        if response.status == 200:
                            return await response.json()
                        elif response.status == 400:
                            try:
                                error_data = await response.json()
                                error_message = error_data.get(
                                    "message", f"API returned status {response.status}"
                                )
                            except Exception:
                                error_message = f"API returned status {response.status} with no readable error message"
                            _LOGGER.error(f"Request failed with 400: {error_message}")
                            raise HomeAssistantError(error_message)
                        elif response.status == 401:
                            _LOGGER.warning(
                                "401 Unauthorized detected, attempting re-authentication..."
                            )
                            if await self.authenticate():
                                headers["Session-Id"] = self.session_id
                                continue
                            else:
                                _LOGGER.error("Re-authentication failed")
                                return None
                        elif response.status == 500:
                            _LOGGER.debug(f"Attempt {attempt + 1} failed, retrying...")
                            await asyncio.sleep(backoff)
                            backoff *= BACKOFF_MULTIPLIER
                        else:
                            _LOGGER.error(f"Request failed: {response.status}")
                            return None
            except Exception as e:
                _LOGGER.error(f"Request error: {str(e)}")
                return None

        _LOGGER.error(f"Max retries ({MAX_RETRIES}) exceeded")
        return None

    async def authenticate(self) -> bool:
        payload = {"login": self.username, "password": self.password}
        response = await self._make_request(
            method="POST",
            endpoint=AUTH_ENDPOINT,
            headers=DEFAULT_HEADERS,
            payload=payload,
        )

        if not response:
            _LOGGER.error("Authentication failed")
            return False

        self.token = response.get("token")
        self.session_id = response.get("sessionID")

        if self.entry_id and self.hass.config_entries.async_get_entry(self.entry_id):
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "session_id": self.session_id,
                    "token": self.token,
                },
            )
            _LOGGER.info("Configuration entry updated with new session ID and token")

        _LOGGER.info("Authentication successful")
        return True

    async def get_accounts(self) -> Optional[List[Dict]]:
        if not self.session_id:
            _LOGGER.error("No active session")
            return None

        headers = {**DEFAULT_HEADERS, "Session-Id": self.session_id}
        return await self._make_request(
            method="GET", endpoint=ACCOUNTS_ENDPOINT, headers=headers
        )

    async def get_receipts(
        self, year: int, account_id: str, organization_id: str
    ) -> Optional[List[Dict]]:
        if not all([self.session_id, account_id, organization_id]):
            _LOGGER.error("Missing required parameters")
            return None

        headers = {
            **DEFAULT_HEADERS,
            "Session-Id": self.session_id,
            "Account-Id": account_id,
            "Organization-Id": organization_id,
        }
        endpoint = f"{RECEIPTS_ENDPOINT}?year={year}"
        return await self._make_request("GET", endpoint, headers=headers)

    async def get_meters(
        self, account_id: str, organization_id: str
    ) -> Optional[List[Dict]]:
        if not all([self.session_id, account_id, organization_id]):
            _LOGGER.error("Missing required parameters")
            return None

        headers = {
            **DEFAULT_HEADERS,
            "Session-Id": self.session_id,
            "Account-Id": account_id,
            "Organization-Id": organization_id,
        }
        return await self._make_request("GET", METERS_ENDPOINT, headers=headers)

    async def send_meter_readings(
        self,
        account_id: str,
        organization_id: str,
        meter_readings: List[Dict[str, Any]],
        confirm: bool = False,
    ) -> Optional[Dict]:
        """Send meter readings to the Kvado API."""
        if not all([self.session_id, account_id, organization_id]):
            _LOGGER.error("Missing required parameters for sending meter readings")
            return None

        headers = {
            **DEFAULT_HEADERS,
            "Session-Id": self.session_id,
            "Account-Id": account_id,
            "Organization-Id": organization_id,
        }
        payload = {
            "confirm": confirm,
            "meters": meter_readings,
        }

        return await self._make_request(
            method="POST",
            endpoint=METERS_ENDPOINT,
            headers=headers,
            payload=payload,
        )
