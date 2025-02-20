import logging
from datetime import datetime, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import (
    DeviceInfo,
    async_get as async_get_device_registry,
)
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import KvadoApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kvado sensors from a config entry."""
    api_client = KvadoApiClient(
        hass=hass,
        username=entry.data["username"],
        password=entry.data["password"],
        token=entry.data.get("token"),
        session_id=entry.data.get("session_id"),
        entry_id=entry.entry_id,
    )
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})["api_client"] = (
        api_client
    )

    selected_accounts = entry.data.get("selected_accounts", [])
    accounts = await api_client.get_accounts()
    if not accounts or "accounts" not in accounts:
        return

    await cleanup_unselected_accounts(
        hass, entry, selected_accounts, accounts["accounts"]
    )

    coordinator = KvadoDataUpdateCoordinator(
        hass, api_client, selected_accounts, accounts["accounts"]
    )
    await coordinator.async_config_entry_first_refresh()

    sensors = []
    for account in accounts["accounts"]:
        account_id = str(account["ID"])
        if account_id in selected_accounts:
            sensors.append(KvadoSensor(coordinator, account))
            meters = await api_client.get_meters(
                account_id, str(account["organizationID"])
            )
            if meters and "meters" in meters:
                sensors.extend(
                    KvadoMeterSensor(coordinator, account_id, meter)
                    for meter in meters["meters"]
                )

    if sensors:
        async_add_entities(sensors, update_before_add=True)

    hass.data[DOMAIN][entry.entry_id]["selected_accounts"] = selected_accounts


async def cleanup_unselected_accounts(
    hass: HomeAssistant,
    entry: ConfigEntry,
    selected_accounts: list,
    all_accounts: list,
) -> None:
    """Remove entities and devices for unselected accounts."""
    device_registry = async_get_device_registry(hass)
    entity_registry = async_get_entity_registry(hass)

    current_selected = set(selected_accounts)
    all_account_ids = {str(account["ID"]) for account in all_accounts}
    unselected_accounts = all_account_ids - current_selected

    for account in all_accounts:
        account_id = str(account["ID"])
        if account_id in unselected_accounts:
            device_identifier = (DOMAIN, f"kvado_account_{account_id}")
            device = device_registry.async_get_device(identifiers={device_identifier})
            if device:
                device_registry.async_remove_device(device.id)

            organization_id = str(account["organizationID"])
            for entity in entity_registry.entities.values():
                if entity.config_entry_id == entry.entry_id and (
                    entity.unique_id.startswith(f"kvado_{organization_id}_{account_id}")
                    or entity.unique_id.startswith(f"kvado_meter_{account_id}_")
                ):
                    entity_registry.async_remove(entity.entity_id)


class KvadoDataUpdateCoordinator(DataUpdateCoordinator):
    """Manager for KVADO API data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: KvadoApiClient,
        selected_accounts: list,
        accounts: list,
    ):
        super().__init__(
            hass, logger=_LOGGER, name="Kvado Sensors", update_interval=SCAN_INTERVAL
        )
        self._api_client = api_client
        self._selected_accounts = [str(acc) for acc in selected_accounts]
        self._accounts = accounts
        self.data = {}

    async def _async_update_data(self):
        try:
            current_year = datetime.now().year
            data = {}
            for account in self._accounts:
                account_id = str(account["ID"])
                if account_id in self._selected_accounts:
                    receipts_data = await self._api_client.get_receipts(
                        year=current_year,
                        account_id=account_id,
                        organization_id=str(account["organizationID"]),
                    )
                    total_pay_amount = (
                        receipts_data.get("info", {})
                        .get("total_pay_amount", {})
                        .get("value")
                        if receipts_data and isinstance(receipts_data, dict)
                        else "N/A"
                    )
                    data[account_id] = total_pay_amount
            return data
        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}")


class KvadoSensor(SensorEntity):
    """Kvado account balance sensor."""

    def __init__(self, coordinator: KvadoDataUpdateCoordinator, account: dict):
        self._coordinator = coordinator
        self._account_id = str(account["ID"])
        self._organization_id = str(account["organizationID"])
        self._account_number = account["account"]
        self._organization_name = account["organizationName"]
        self._address = account["address"]

    @property
    def name(self):
        return self._address

    @property
    def unique_id(self):
        return f"kvado_{self._organization_id}_{self._account_id}"

    @property
    def state(self):
        return self._coordinator.data.get(self._account_id, "N/A")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"kvado_account_{self._account_id}")},
            name=f"{self._organization_name} ({self._address})",
            manufacturer="КВАДО",
            model="Учетная запись",
        )

    @property
    def extra_state_attributes(self):
        return {
            "Account ID": self._account_id,
            "Account Number": self._account_number,
            "Organization ID": self._organization_id,
            "Organization Name": self._organization_name,
            "Address": self._address,
        }


class KvadoMeterSensor(SensorEntity):
    """Kvado meter sensor."""

    def __init__(
        self, coordinator: KvadoDataUpdateCoordinator, account_id: str, meter: dict
    ):
        self._coordinator = coordinator
        self._account_id = account_id
        self._meter_id = str(meter["ID"])
        self._meter_type = meter["type"]
        self._meter_number = meter["number"]
        self._unit = meter["unit"]
        self._details = meter["values"][0]["details"] if meter["values"] else "No data"
        self._state = meter["values"][0]["value"] if meter["values"] else "N/A"
        self._attr_native_unit_of_measurement = self._unit

    @property
    def name(self):
        return f"{self._meter_type} {self._meter_number}"

    @property
    def unique_id(self):
        return f"kvado_meter_{self._account_id}_{self._meter_id}"

    @property
    def state(self):
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, f"kvado_account_{self._account_id}")})

    @property
    def extra_state_attributes(self):
        return {
            "Meter ID": self._meter_id,
            "Meter Type": self._meter_type,
            "Meter Number": self._meter_number,
            "Unit": self._unit,
            "Details": self._details,
        }
