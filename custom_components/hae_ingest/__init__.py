from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_flow import webhook_async_remove_entry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, EVENT_PREFIX, SIGNAL_UPDATE
from .parser import COLLECTIONS, metric_series, parse_collection, parse_metrics
from .stats import async_import_metric_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

FLUSH_DELAY = 5


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook.async_register(
        hass, DOMAIN, "Health Auto Export Ingest", entry.data[CONF_WEBHOOK_ID], handle_webhook
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    domain_data = hass.data.get(DOMAIN)
    if domain_data and domain_data.get("flush_unsub"):
        domain_data["flush_unsub"]()
        domain_data["flush_unsub"] = None
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
        _buffer_metric_series(hass, metric_series(data["metrics"]))
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


def _buffer_metric_series(hass: HomeAssistant, series_list) -> None:
    if not series_list:
        return
    domain_data = hass.data.setdefault(
        DOMAIN, {"series": {}, "flush_unsub": None, "lock": asyncio.Lock()}
    )
    for series in series_list:
        buffered = domain_data["series"].setdefault(
            series["key"],
            {"name": series["name"], "unit": series.get("unit"), "points": []},
        )
        buffered["points"].extend(series["points"])
        if not buffered.get("unit") and series.get("unit"):
            buffered["unit"] = series["unit"]
    if domain_data["flush_unsub"] is not None:
        domain_data["flush_unsub"]()

    async def _flush(_now) -> None:
        domain_data["flush_unsub"] = None
        pending = domain_data["series"]
        domain_data["series"] = {}
        flush_list = [{"key": key, **value} for key, value in pending.items()]
        _LOGGER.debug("Flushing %d buffered statistic series", len(flush_list))
        async with domain_data["lock"]:
            await async_import_metric_statistics(hass, flush_list)

    domain_data["flush_unsub"] = async_call_later(hass, FLUSH_DELAY, _flush)
