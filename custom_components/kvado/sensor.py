"""Kvado Account and Meter integration."""

from datetime import datetime, timedelta
import logging

import voluptuous as vol

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.helpers.entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KvadoApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)

SEND_METER_READINGS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("meter_readings"): vol.All(
            [
                vol.Schema(
                    {
                        vol.Required("entity_id"): str,
                        vol.Required(
                            "newValue", msg="newValue must be a float"
                        ): vol.Coerce(float),
                    }
                )
            ],
            vol.Length(min=1),
        ),
        vol.Optional("confirm", default=False): bool,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configure Kvado integration sensors and services."""
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
        _LOGGER.error("Failed to fetch accounts from Kvado API")
        return

    await cleanup_unselected_accounts(
        hass, entry, selected_accounts, accounts["accounts"]
    )

    coordinator = KvadoDataUpdateCoordinator(
        hass, api_client, selected_accounts, accounts["accounts"]
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as e:
        _LOGGER.error("Failed to initialize coordinator: {}".format(e))
        return

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

    async def handle_send_meter_readings(call: ServiceCall) -> None:
        """Process meter reading submissions via service call."""
        _LOGGER.debug("Raw service call data: {}".format(call.data))

        try:
            validated_data = SEND_METER_READINGS_SCHEMA(dict(call.data))
        except vol.Invalid as e:
            _LOGGER.error("Service call validation failed: {}".format(e))
            raise HomeAssistantError("Invalid input: {}".format(e))

        entity_id = validated_data["entity_id"]
        meter_readings_input = validated_data["meter_readings"]
        confirm = validated_data["confirm"]

        entity = hass.states.get(entity_id)
        if (
            not entity
            or entity.domain != "sensor"
            or "Account ID" not in entity.attributes
        ):
            _LOGGER.error(
                "Invalid entity_id {}: must be a valid KvadoSensor".format(entity_id)
            )
            raise HomeAssistantError(
                "Invalid entity_id {}: must be a valid KvadoSensor".format(entity_id)
            )

        account_id = entity.attributes.get("Account ID")
        organization_id = entity.attributes.get("Organization ID")

        meter_readings = []
        for reading in meter_readings_input:
            meter_entity = hass.states.get(reading["entity_id"])
            if (
                not meter_entity
                or meter_entity.domain != "sensor"
                or "Meter ID" not in meter_entity.attributes
            ):
                _LOGGER.error(
                    "Invalid meter entity_id {}: must be a valid KvadoMeterSensor".format(
                        reading["entity_id"]
                    )
                )
                raise HomeAssistantError(
                    "Invalid meter entity_id {}: must be a valid KvadoMeterSensor".format(
                        reading["entity_id"]
                    )
                )
            meter_id = meter_entity.attributes.get("Meter ID")

            meter_readings.append(
                {
                    "ID": meter_id,
                    "values": [
                        {"systemCatalogBetID": 1, "newValue": reading["newValue"]}
                    ],
                }
            )

        result = await api_client.send_meter_readings(
            account_id=account_id,
            organization_id=organization_id,
            meter_readings=meter_readings,
            confirm=confirm,
        )
        if result is None:
            _LOGGER.error(
                "Failed to send meter readings for account {}".format(account_id)
            )
            raise HomeAssistantError(
                "Failed to send meter readings. Check Home Assistant logs"
            )
        _LOGGER.info(
            "Successfully sent meter readings for account {}: {}".format(
                account_id, result
            )
        )

    try:
        hass.services.async_register(
            DOMAIN,
            "send_meter_readings",
            handle_send_meter_readings,
            schema=SEND_METER_READINGS_SCHEMA,
        )
        _LOGGER.debug("Successfully registered kvado.send_meter_readings service")
    except Exception as e:
        _LOGGER.error("Failed to register send_meter_readings service: {}".format(e))
        raise


async def cleanup_unselected_accounts(
    hass: HomeAssistant,
    entry: ConfigEntry,
    selected_accounts: list,
    all_accounts: list,
) -> None:
    """Clean up entities and devices for deselected accounts."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    current_selected = set(selected_accounts)
    unselected_accounts = {
        str(account["ID"]) for account in all_accounts
    } - current_selected

    for account_id in unselected_accounts:
        device_identifier = (DOMAIN, f"kvado_account_{account_id}")
        device = device_registry.async_get_device(identifiers={device_identifier})
        if device:
            device_registry.async_remove_device(device.id)

        for account in all_accounts:
            if str(account["ID"]) == account_id:
                organization_id = str(account["organizationID"])
                break
        else:
            continue

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
        self.data = {"accounts": {}, "meters": {}}

    async def _async_update_data(self):
        """Fetch and update data from Kvado API."""
        _LOGGER.debug("Coordinator triggered: Starting data update")
        try:
            current_year = datetime.now().year
            data = {"accounts": {}, "meters": {}}
            for account in self._accounts:
                account_id = str(account["ID"])
                if account_id in self._selected_accounts:
                    _LOGGER.debug("Fetching receipts for account {}".format(account_id))
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
                    data["accounts"][account_id] = total_pay_amount

                    _LOGGER.debug("Fetching meters for account {}".format(account_id))
                    meters = await self._api_client.get_meters(
                        account_id=account_id,
                        organization_id=str(account["organizationID"]),
                    )
                    if meters and "meters" in meters:
                        for meter in meters["meters"]:
                            meter_id = str(meter["ID"])
                            value = meter.get("values", [{}])[0].get("value", "N/A")
                            data["meters"][meter_id] = value
            _LOGGER.debug("Coordinator update completed: {}".format(data))
            return data
        except Exception as e:
            _LOGGER.error("Coordinator update failed: {}".format(e))
            raise UpdateFailed("Error fetching data: {}".format(e))


class KvadoSensor(SensorEntity):
    """Kvado account balance sensor."""

    def __init__(self, coordinator: KvadoDataUpdateCoordinator, account: dict):
        self._coordinator = coordinator
        self._account_id = str(account["ID"])
        self._organization_id = str(account["organizationID"])
        self._account_number = account["account"]
        self._organization_name = account["organizationName"]
        self._address = account["address"]
        self._account_data = "N/A"

    async def async_added_to_hass(self) -> None:
        """Register entity with Home Assistant and set up updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Update sensor state from coordinator data."""
        self._account_data = self._coordinator.data["accounts"].get(
            self._account_id, "N/A"
        )
        _LOGGER.debug(
            "Updated account data for {}: {}".format(self.unique_id, self._account_data)
        )
        self.async_write_ha_state()

    @property
    def name(self):
        return self._address

    @property
    def unique_id(self):
        return f"kvado_{self._organization_id}_{self._account_id}"

    @property
    def state(self):
        return self._account_data

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
    ) -> None:
        self._coordinator = coordinator
        self._account_id = account_id
        self._meter_id = str(meter["ID"])
        self._meter_type = meter["type"]
        self._meter_number = meter["number"]
        self._unit = meter["unit"]
        self._details = (
            meter["values"][0]["details"] if meter.get("values") else "No data"
        )
        self._attr_native_unit_of_measurement = self._unit
        self._meter_data = "N/A"

    async def async_added_to_hass(self) -> None:
        """Register entity with Home Assistant and set up updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Update sensor state from coordinator data."""
        self._meter_data = self._coordinator.data["meters"].get(self._meter_id, "N/A")
        _LOGGER.debug(
            "Updated meter data for {}: {}".format(self.unique_id, self._meter_data)
        )
        self.async_write_ha_state()

    @property
    def name(self):
        return f"{self._meter_type} {self._meter_number}"

    @property
    def unique_id(self):
        return f"kvado_meter_{self._account_id}_{self._meter_id}"

    @property
    def state(self):
        return self._meter_data

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
