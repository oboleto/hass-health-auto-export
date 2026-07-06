from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_flow import webhook_async_remove_entry
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, EVENT_PREFIX, SIGNAL_UPDATE
from .parser import COLLECTIONS, metric_series, parse_collection, parse_metrics
from .stats import async_import_metric_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook.async_register(
        hass, DOMAIN, "Health Auto Export Ingest", entry.data[CONF_WEBHOOK_ID], handle_webhook
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async_remove_entry = webhook_async_remove_entry


async def handle_webhook(hass: HomeAssistant, webhook_id: str, request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.Response(status=400, text="invalid json")
    if not isinstance(payload, dict):
        return web.Response(status=400, text="unexpected payload")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    records = []
    if isinstance(data.get("metrics"), list):
        records.extend(parse_metrics(data["metrics"]))
        hass.async_create_task(
            async_import_metric_statistics(hass, metric_series(data["metrics"]))
        )
    for collection_key, singular in COLLECTIONS.items():
        items = data.get(collection_key)
        if isinstance(items, list) and items:
            events, collection_records = parse_collection(collection_key, items)
            for event_data in events:
                hass.bus.async_fire(f"{EVENT_PREFIX}{singular}", event_data)
            records.extend(collection_records)
    if records:
        async_dispatcher_send(hass, SIGNAL_UPDATE, records)
    _LOGGER.debug("Webhook %s processed: %d sensor records", webhook_id, len(records))
    return web.json_response({"sensors_updated": len(records)})
