import logging
from datetime import datetime, timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from .api import KvadoApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    api_client = KvadoApiClient(
        hass=hass,
        username=entry.data["username"],
        password=entry.data["password"],
        token=entry.data.get("token"),
        session_id=entry.data.get("session_id"),
        entry_id=entry.entry_id,
    )
    hass.data[DOMAIN][entry.entry_id]["api_client"] = api_client

    selected_accounts = hass.data[DOMAIN][entry.entry_id]["selected_accounts"]
    accounts = await api_client.get_accounts()
    if not accounts or "accounts" not in accounts:
        _LOGGER.error("Failed to fetch accounts during sensor setup.")
        return

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
                for meter in meters["meters"]:
                    sensors.append(KvadoMeterSensor(coordinator, account_id, meter))

    async_add_entities(sensors, True)


class KvadoDataUpdateCoordinator(DataUpdateCoordinator):
    """Manager for receiving data from KVADO API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: KvadoApiClient,
        selected_accounts: list,
        accounts: list,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Kvado Sensors",
            update_interval=SCAN_INTERVAL,
        )
        self._api_client = api_client
        self._selected_accounts = [str(acc) for acc in selected_accounts]
        self._accounts = accounts
        self.data = {}

    async def _async_update_data(self):
        """Fetch data from the API."""
        try:
            current_year = datetime.now().year
            data = {}
            for account in self._accounts:
                account_id = str(account["ID"])
                organization_id = str(account["organizationID"])
                if account_id in self._selected_accounts:
                    receipts_data = await self._api_client.get_receipts(
                        year=current_year,
                        account_id=account_id,
                        organization_id=organization_id,
                    )
                    if receipts_data and isinstance(receipts_data, dict):
                        total_pay_amount = (
                            receipts_data.get("info", {})
                            .get("total_pay_amount", {})
                            .get("value")
                        )
                        data[account_id] = total_pay_amount or "N/A"
                    else:
                        data[account_id] = "N/A"
                        _LOGGER.warning(
                            f"No receipts data found for account {account['account']} in year {current_year}"
                        )
            return data
        except Exception as e:
            raise UpdateFailed(f"Error fetching data: {e}")


class KvadoSensor(SensorEntity):
    """Representation of a Kvado account balance sensor."""

    def __init__(self, coordinator: KvadoDataUpdateCoordinator, account: dict):
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._account = account
        self._account_id = str(account["ID"])
        self._organization_id = str(account["organizationID"])
        self._account_number = account["account"]
        self._organization_name = account["organizationName"]
        self._address = account["address"]

    @property
    def name(self):
        return f"{self._address}"

    @property
    def unique_id(self):
        return f"kvado_{self._organization_id}_{self._account_id}"

    @property
    def state(self):
        return self._coordinator.data.get(self._account_id, "N/A")

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
    """Representation of a Kvado meter sensor."""

    def __init__(
        self, coordinator: KvadoDataUpdateCoordinator, account_id: str, meter: dict
    ):
        """Initialize the meter sensor."""
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
    def extra_state_attributes(self):
        return {
            "Meter ID": self._meter_id,
            "Meter Type": self._meter_type,
            "Meter Number": self._meter_number,
            "Unit": self._unit,
            "Details": self._details,
        }
