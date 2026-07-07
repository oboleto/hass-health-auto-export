from __future__ import annotations

from homeassistant.components import webhook
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OPTION_SENSORS, SIGNAL_UPDATE
from .parser import COLLECTIONS

EXCLUDED_RESTORE_ATTRS = {
    "unit_of_measurement",
    "friendly_name",
    "state_class",
    "device_class",
    "icon",
}

_ITEM_SINGULARS = tuple(COLLECTIONS.values())
_ITEM_ROLE_SUFFIXES = (
    ("_last_dose", "last_dose"),
    ("_last", "last"),
    ("_doses", "doses"),
    ("_sessions", "sessions"),
    ("_duration", "duration"),
    ("_energy", "energy"),
    ("_distance", "distance"),
    ("_occurrences", "occurrences"),
)
_ITEM_ROLE_NAMES = {
    "status": "Status",
    "last_dose": "Last dose",
    "last": "Last seen",
    "doses": "Doses",
    "sessions": "Sessions",
    "duration": "Duration",
    "energy": "Active energy",
    "distance": "Distance",
    "occurrences": "Occurrences",
}
_STATUS_NAMES = {
    "medication": "Status",
    "workout": "Latest",
    "symptom": "Severity",
    "cycle_tracking": "Value",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: dict[str, HealthAutoExportSensor] = {}

    restored = [
        HealthAutoExportSensor(entry, meta)
        for meta in entry.options.get(OPTION_SENSORS, [])
        if isinstance(meta, dict) and meta.get("key")
    ]
    for entity in restored:
        entities[entity.meta["key"]] = entity
    async_add_entities([WebhookUrlSensor(hass, entry), *restored])

    @callback
    def _handle_records(records) -> None:
        new_entities = []
        for record in records:
            key = record["key"]
            existing = entities.get(key)
            if existing is not None:
                existing.update_from_record(record)
                continue
            meta = {
                "key": key,
                "name": record["name"],
                "unit": record.get("unit"),
                "state_class": record.get("state_class"),
            }
            if record.get("device_class"):
                meta["device_class"] = record["device_class"]
            attrs = record.get("attributes") or {}
            if _item_device(key) and attrs.get("item_name"):
                meta["device_name"] = attrs["item_name"]
            entity = HealthAutoExportSensor(entry, meta, record)
            entities[key] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    OPTION_SENSORS: [e.meta for e in entities.values()],
                },
            )

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_UPDATE, _handle_records)
    )


class WebhookUrlSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Webhook URL"
    _attr_icon = "mdi:webhook"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        webhook_id = entry.data[CONF_WEBHOOK_ID]
        self._attr_unique_id = f"{entry.entry_id}_webhook_url"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Health Auto Export",
            manufacturer="HealthyApps",
            model="Health Auto Export",
        )
        path = f"/api/webhook/{webhook_id}"
        try:
            url = webhook.async_generate_url(hass, webhook_id)
        except Exception:
            url = path
        self._attr_native_value = url[:255]
        self._attr_extra_state_attributes = {"webhook_id": webhook_id, "path": path}


class HealthAutoExportSensor(RestoreSensor):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, meta: dict, record: dict | None = None) -> None:
        self.meta = meta
        key = meta["key"]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = meta.get("name") or key
        self._attr_native_unit_of_measurement = meta.get("unit")
        if meta.get("state_class") == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._is_timestamp = meta.get("device_class") == "timestamp"
        if self._is_timestamp:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        item = _item_device(key)
        if item:
            singular, slug, role = item
            if role == "status":
                self._attr_name = _STATUS_NAMES.get(singular, "Status")
            else:
                self._attr_name = _ITEM_ROLE_NAMES.get(role, role.replace("_", " ").title())
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_{singular}_{slug}")},
                name=meta.get("device_name") or slug.replace("_", " ").title(),
                manufacturer="HealthyApps",
                model=singular.replace("_", " ").title(),
                via_device=(DOMAIN, entry.entry_id),
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name="Health Auto Export",
                manufacturer="HealthyApps",
                model="Health Auto Export",
            )
        self._attr_extra_state_attributes = {}
        if record is not None:
            self._apply(record)

    def _apply(self, record: dict) -> None:
        value = record.get("value")
        if isinstance(value, float):
            value = round(value, 4)
        if self._is_timestamp and isinstance(value, str):
            value = dt_util.parse_datetime(value)
        self._attr_native_value = value
        self._attr_extra_state_attributes = record.get("attributes") or {}

    @callback
    def update_from_record(self, record: dict) -> None:
        self._apply(record)
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_native_value is not None:
            return
        sensor_data = await self.async_get_last_sensor_data()
        if sensor_data is not None:
            value = sensor_data.native_value
            if self._is_timestamp and isinstance(value, str):
                value = dt_util.parse_datetime(value)
            self._attr_native_value = value
        state = await self.async_get_last_state()
        if state is not None:
            self._attr_extra_state_attributes = {
                k: v
                for k, v in state.attributes.items()
                if k not in EXCLUDED_RESTORE_ATTRS
            }


def _item_device(key: str):
    for singular in _ITEM_SINGULARS:
        prefix = f"{singular}_"
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        for suffix, role in _ITEM_ROLE_SUFFIXES:
            if suffix and rest.endswith(suffix):
                slug = rest[: -len(suffix)]
                if slug:
                    return singular, slug, role
        return singular, rest, "status"
    return None
