import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import KvadoApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): cv.string,
        vol.Required("password"): cv.string,
    }
)

ACCOUNT_SELECTION_SCHEMA = vol.Schema(
    {
        vol.Required("selected_accounts"): vol.All(
            cv.multi_select, vol.Length(min=1)
        ),
    }
)


class CannotConnect(HomeAssistantError):


class InvalidAuth(HomeAssistantError):


class KvadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self):
        self.api_client = None
        self.accounts = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}

        if user_input is not None:
            try:
                _LOGGER.debug(f"User input received: {user_input}")

                self.api_client = KvadoApiClient(
                    hass=self.hass,
                    username=user_input["username"],
                    password=user_input["password"],
                )
                _LOGGER.debug(
                    f"Initialized API client with username: {user_input['username']}"
                )

                _LOGGER.debug("Attempting to authenticate...")
                authenticated = await self.api_client.authenticate()
                if not authenticated:
                    _LOGGER.error("Authentication failed.")
                    raise InvalidAuth

                _LOGGER.debug("Fetching accounts...")
                accounts = await self.api_client.get_accounts()
                if not accounts or "accounts" not in accounts:
                    _LOGGER.error("Failed to fetch accounts.")
                    raise CannotConnect

                self.accounts = [
                    {
                        "id": account["ID"],
                        "account_number": account["account"],
                        "organization_name": account["organizationName"],
                        "address": account["address"],
                    }
                    for account in accounts["accounts"]
                ]
                _LOGGER.debug(f"Fetched {len(self.accounts)} accounts.")

                return await self.async_step_account_selection()

            except CannotConnect:
                _LOGGER.error("Cannot connect to the API.")
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                _LOGGER.error("Invalid authentication credentials.")
                errors["base"] = "invalid_auth"
            except Exception as e: 
                _LOGGER.exception(f"Unexpected exception: {e}")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_account_selection(self, user_input=None) -> FlowResult:
        if user_input is not None:
            selected_accounts = user_input["selected_accounts"]
            _LOGGER.debug(f"User selected accounts: {selected_accounts}")

            return self.async_create_entry(
                title=self.api_client.username,
                data={
                    "username": self.api_client.username,
                    "password": self.api_client.password,
                    "token": self.api_client.token,
                    "session_id": self.api_client.session_id,
                    "selected_accounts": selected_accounts,
                },
            )

        account_options = {
            str(
                account["id"]
            ): f"{account['account_number']} - {account['organization_name']}"
            for account in self.accounts
        }

        _LOGGER.debug(f"Account options for selection: {account_options}")

        return self.async_show_form(
            step_id="account_selection",
            data_schema=vol.Schema(
                {
                    vol.Required("selected_accounts"): vol.All(
                        cv.multi_select(account_options),
                        vol.Length(min=1),
                    ),
                }
            ),
        )
