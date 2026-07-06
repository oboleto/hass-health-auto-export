from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OPTION_SENSORS, SIGNAL_UPDATE

EXCLUDED_RESTORE_ATTRS = {
    "unit_of_measurement",
    "friendly_name",
    "state_class",
    "device_class",
    "icon",
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
    if restored:
        async_add_entities(restored)

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


class HealthAutoExportSensor(RestoreSensor):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, meta: dict, record: dict | None = None) -> None:
        self.meta = meta
        self._attr_unique_id = f"{entry.entry_id}_{meta['key']}"
        self._attr_name = meta.get("name") or meta["key"]
        self._attr_native_unit_of_measurement = meta.get("unit")
        if meta.get("state_class") == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT
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
            self._attr_native_value = sensor_data.native_value
        state = await self.async_get_last_state()
        if state is not None:
            self._attr_extra_state_attributes = {
                k: v
                for k, v in state.attributes.items()
                if k not in EXCLUDED_RESTORE_ATTRS
            }
