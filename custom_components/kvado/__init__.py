import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import KvadoApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kvado from a config entry."""
    username = entry.data.get("username")
    password = entry.data.get("password")
    token = entry.data.get("token")
    session_id = entry.data.get("session_id")
    selected_accounts = entry.data.get("selected_accounts", [])

    api_client = KvadoApiClient(
        hass=hass,
        username=username,
        password=password,
        token=token,
        session_id=session_id,
    )

    authenticated = await api_client.authenticate()
    if not authenticated:
        raise ConfigEntryNotReady("Failed to authenticate with Kvado API.")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api_client": api_client,
        "selected_accounts": selected_accounts,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
