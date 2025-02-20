import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""


class KvadoBaseFlow:
    """Base class for Kvado config and options flows."""

    def __init__(self):
        self.api_client = None
        self.accounts = []

    async def _authenticate_and_fetch_accounts(
        self, hass: HomeAssistant, username: str, password: str
    ) -> None:
        """Authenticate and fetch accounts, raising errors on failure."""
        self.api_client = KvadoApiClient(
            hass=hass, username=username, password=password
        )
        if not await self.api_client.authenticate():
            _LOGGER.error("Authentication failed")
            raise InvalidAuth
        accounts = await self.api_client.get_accounts()
        if not accounts or "accounts" not in accounts:
            _LOGGER.error("Failed to fetch accounts")
            raise CannotConnect
        self.accounts = [
            {
                "id": str(account["ID"]),
                "account_number": account["account"],
                "organization_name": account["organizationName"],
                "address": account["address"],
            }
            for account in accounts["accounts"]
        ]


class KvadoConfigFlow(KvadoBaseFlow, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kvado."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle initial user input for username and password."""
        errors = {}
        if user_input is not None:
            _LOGGER.debug(f"User input: {user_input}")
            try:
                await self._authenticate_and_fetch_accounts(
                    self.hass, user_input["username"], user_input["password"]
                )
                return await self.async_step_account_selection()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception as e:
                _LOGGER.exception(f"Unexpected error: {e}")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_account_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle account selection step."""
        if user_input is not None:
            selected_accounts = user_input["selected_accounts"]
            _LOGGER.debug(f"Selected accounts: {selected_accounts}")
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
            account[
                "id"
            ]: f"{account['account_number']} - {account['organization_name']}"
            for account in self.accounts
        }
        return self.async_show_form(
            step_id="account_selection",
            data_schema=vol.Schema(
                {
                    vol.Optional("selected_accounts", default=[]): cv.multi_select(
                        account_options
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "KvadoOptionsFlowHandler":
        """Get the options flow handler."""
        return KvadoOptionsFlowHandler(config_entry)


class KvadoOptionsFlowHandler(KvadoBaseFlow, config_entries.OptionsFlow):
    """Handle options flow for Kvado reconfiguration."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Initialize options flow and fetch accounts."""
        errors = {}
        try:
            await self._authenticate_and_fetch_accounts(
                self.hass,
                self.config_entry.data["username"],
                self.config_entry.data["password"],
            )
            return await self.async_step_account_selection()
        except CannotConnect:
            _LOGGER.error("Cannot connect to API during options flow")
            errors["base"] = "cannot_connect"
            return self.async_abort(reason="cannot_connect")
        except InvalidAuth:
            _LOGGER.error("Invalid authentication during options flow")
            errors["base"] = "invalid_auth"
            return self.async_abort(reason="invalid_auth")
        except Exception as e:
            _LOGGER.exception(f"Unexpected error in options flow: {e}")
            errors["base"] = "unknown"
            return self.async_abort(reason="unknown")

    async def async_step_account_selection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle account selection during reconfiguration."""
        if user_input is not None:
            selected_accounts = user_input["selected_accounts"]
            _LOGGER.debug(
                f"Selected accounts during reconfiguration: {selected_accounts}"
            )
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, "selected_accounts": selected_accounts},
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title=self.config_entry.title, data={})

        account_options = {
            account[
                "id"
            ]: f"{account['account_number']} - {account['organization_name']}"
            for account in self.accounts
        }
        current_selected = [
            str(account_id)
            for account_id in self.config_entry.data.get("selected_accounts", [])
        ]
        return self.async_show_form(
            step_id="account_selection",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "selected_accounts", default=current_selected
                    ): cv.multi_select(account_options),
                }
            ),
        )
