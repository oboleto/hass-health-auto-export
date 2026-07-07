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

from .const import DOMAIN, EVENT_PREFIX, OPTION_MEDICATION_MERGES, SIGNAL_UPDATE
from .parser import (
    COLLECTIONS,
    medication_series,
    metric_series,
    parse_collection,
    parse_merge_rules,
    parse_metrics,
    slugify,
)
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
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "Webhook payload received: %s",
            {k: len(v) for k, v in data.items() if isinstance(v, list)} or list(data),
        )
    records = []
    if isinstance(data.get("metrics"), list):
        records.extend(parse_metrics(data["metrics"]))
        series_list = metric_series(data["metrics"])
        for series in series_list:
            _LOGGER.debug(
                "Metric %s (%s): %d points, %s .. %s",
                series["key"],
                series.get("unit"),
                len(series["points"]),
                series["points"][0][0],
                series["points"][-1][0],
            )
        series_keys = {s["key"] for s in series_list}
        if "sleep_total" in series_keys:
            series_keys.add("sleep_analysis")
        skipped = {
            slugify(m.get("name") or "unknown")
            for m in data["metrics"]
            if isinstance(m, dict)
        } - series_keys
        if skipped:
            _LOGGER.debug("Metrics without importable points (no date/qty parsed): %s", sorted(skipped))
        _buffer_metric_series(hass, series_list)
    for collection_key, singular in COLLECTIONS.items():
        items = data.get(collection_key)
        if isinstance(items, list) and items:
            merges = _medication_merges(hass) if collection_key == "medications" else None
            events, collection_records = parse_collection(collection_key, items, merges)
            for event_data in events:
                hass.bus.async_fire(f"{EVENT_PREFIX}{singular}", event_data)
            records.extend(collection_records)
            if collection_key == "medications":
                _buffer_metric_series(hass, medication_series(items, merges))
    if records:
        async_dispatcher_send(hass, SIGNAL_UPDATE, records)
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "Webhook processed, %d sensor records: %s",
            len(records),
            {r["key"]: r["value"] for r in records},
        )
    return web.json_response({"sensors_updated": len(records)})


def _medication_merges(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries(DOMAIN):
        raw = entry.options.get(OPTION_MEDICATION_MERGES)
        if isinstance(raw, dict):
            rules = {
                slugify(k): slugify(v)
                for k, v in raw.items()
                if slugify(k) and slugify(v) and slugify(k) != slugify(v)
            }
        else:
            rules = parse_merge_rules(raw)
        if rules:
            return rules
    return None


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
        _LOGGER.debug(
            "Flushing statistics buffer: %s",
            {s["key"]: len(s["points"]) for s in flush_list},
        )
        async with domain_data["lock"]:
            await async_import_metric_statistics(hass, flush_list)

    domain_data["flush_unsub"] = async_call_later(hass, FLUSH_DELAY, _flush)
