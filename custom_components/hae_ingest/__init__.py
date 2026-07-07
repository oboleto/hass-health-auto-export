from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_DATA_TYPE,
    DOMAIN,
    EVENT_PREFIX,
    OPTION_MEDICATION_MERGES,
    SIGNAL_UPDATE,
)
from .parser import (
    COLLECTIONS,
    annotate_heart_rate_notifications,
    collection_series,
    metric_series,
    parse_collection,
    parse_merge_rules,
    parse_metrics,
    slugify,
    state_of_mind_series,
)
from .stats import async_import_metric_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

FLUSH_DELAY = 5


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "series": {},
        "flush_unsub": None,
        "lock": asyncio.Lock(),
    }
    webhook.async_register(
        hass,
        DOMAIN,
        entry.title or "Health Auto Export Ingest",
        entry.data[CONF_WEBHOOK_ID],
        handle_webhook,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if entry_data and entry_data.get("flush_unsub"):
        entry_data["flush_unsub"]()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def handle_webhook(hass: HomeAssistant, webhook_id: str, request) -> web.Response:
    entry = next(
        (
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_WEBHOOK_ID) == webhook_id
        ),
        None,
    )
    if entry is None:
        return web.Response(status=404, text="unknown webhook")
    try:
        payload = await request.json()
    except ValueError:
        return web.Response(status=400, text="invalid json")
    if not isinstance(payload, dict):
        return web.Response(status=400, text="unexpected payload")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    data_type = entry.data.get(CONF_DATA_TYPE)
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "Webhook payload received (%s): %s",
            data_type or "all",
            {k: len(v) for k, v in data.items() if isinstance(v, list)} or list(data),
        )
    records = []
    if _wants(data_type, "metrics") and isinstance(data.get("metrics"), list):
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
        _buffer_metric_series(hass, entry.entry_id, series_list)
    for collection_key, singular in COLLECTIONS.items():
        if not _wants(data_type, collection_key):
            continue
        items = data.get(collection_key)
        if isinstance(items, list) and items:
            if collection_key == "heartRateNotifications":
                annotate_heart_rate_notifications(items)
            merges = _entry_merges(entry) if collection_key == "medications" else None
            events, collection_records = parse_collection(collection_key, items, merges)
            for event_data in events:
                hass.bus.async_fire(f"{EVENT_PREFIX}{singular}", event_data)
            records.extend(collection_records)
            _buffer_metric_series(
                hass, entry.entry_id, collection_series(collection_key, items, merges)
            )
            if collection_key == "stateOfMind":
                _buffer_metric_series(
                    hass, entry.entry_id, state_of_mind_series(items)
                )
    if records:
        async_dispatcher_send(hass, f"{SIGNAL_UPDATE}_{entry.entry_id}", records)
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "Webhook processed, %d sensor records: %s",
            len(records),
            {r["key"]: r["value"] for r in records},
        )
    return web.json_response({"sensors_updated": len(records)})


def _wants(data_type, key) -> bool:
    return data_type is None or data_type == key


def _entry_merges(entry: ConfigEntry):
    raw = entry.options.get(OPTION_MEDICATION_MERGES)
    if isinstance(raw, dict):
        rules = {
            slugify(k): slugify(v)
            for k, v in raw.items()
            if slugify(k) and slugify(v) and slugify(k) != slugify(v)
        }
    else:
        rules = parse_merge_rules(raw)
    return rules or None


def _buffer_metric_series(hass: HomeAssistant, entry_id: str, series_list) -> None:
    if not series_list:
        return
    domain_data = hass.data[DOMAIN][entry_id]
    for series in series_list:
        buffered = domain_data["series"].setdefault(
            series["key"],
            {
                "name": series["name"],
                "unit": series.get("unit"),
                "kind": series.get("kind"),
                "points": [],
            },
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
            await async_import_metric_statistics(hass, entry_id, flush_list)

    domain_data["flush_unsub"] = async_call_later(hass, FLUSH_DELAY, _flush)
