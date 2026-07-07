from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN

try:
    from homeassistant.components.recorder.statistics import async_import_statistics
except ImportError:  # pragma: no cover
    async_import_statistics = None

_LOGGER = logging.getLogger(__name__)

SUM_UNITS = {"count", "kcal", "kJ", "km", "mi", "m", "ft", "yd", "L", "mL", "IU", "g", "mg", "µg"}

EPOCH = datetime(1970, 1, 2, tzinfo=timezone.utc)


async def async_import_metric_statistics(
    hass: HomeAssistant, entry_id: str, series_list
) -> None:
    if not series_list or "recorder" not in hass.config.components:
        return
    for series in series_list:
        try:
            await _import_series(hass, entry_id, series)
        except Exception:
            _LOGGER.exception("Failed to import statistics for %s", series["key"])
    try:
        await get_instance(hass).async_block_till_done()
    except Exception:
        _LOGGER.debug("Could not wait for recorder queue drain", exc_info=True)


async def _import_series(hass: HomeAssistant, entry_id: str, series) -> None:
    buckets = _hourly_buckets(series["points"])
    if not buckets:
        return
    is_sum = series.get("unit") in SUM_UNITS
    entity_id = None
    if series.get("kind") == "metric" and async_import_statistics is not None:
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry_id}_{series['key']}"
        )
    if entity_id:
        statistic_id = entity_id
        source = "recorder"
        name = None
    else:
        statistic_id = f"{DOMAIN}:{series['key']}"
        source = DOMAIN
        name = series["name"]
    metadata = StatisticMetaData(
        source=source,
        statistic_id=statistic_id,
        name=name,
        unit_of_measurement=series.get("unit"),
        has_mean=not is_sum,
        has_sum=is_sum,
    )
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        metadata["mean_type"] = (
            StatisticMeanType.NONE if is_sum else StatisticMeanType.ARITHMETIC
        )
    except ImportError:
        pass
    metadata.setdefault("unit_class", None)
    if is_sum:
        stats = await _sum_stats(hass, statistic_id, buckets)
    else:
        stats = [
            StatisticData(
                start=start,
                mean=sum(values) / len(values),
                min=min(values),
                max=max(values),
            )
            for start, values in buckets.items()
        ]
    if not stats:
        _LOGGER.debug("No statistic rows to import for %s", statistic_id)
        return
    _LOGGER.debug(
        "Importing %d statistic rows for %s (%s, %s) as %s (%s .. %s)",
        len(stats),
        statistic_id,
        source,
        series.get("kind"),
        "sum" if is_sum else "mean",
        stats[0]["start"],
        stats[-1]["start"],
    )
    if source == "recorder":
        async_import_statistics(hass, metadata, stats)
    else:
        async_add_external_statistics(hass, metadata, stats)


async def _sum_stats(hass: HomeAssistant, statistic_id: str, buckets) -> list[StatisticData]:
    new_totals = {
        start.timestamp(): (start, sum(values)) for start, values in buckets.items()
    }
    earliest_new = min(new_totals)

    def _fetch_existing():
        return statistics_during_period(
            hass, EPOCH, None, {statistic_id}, "hour", None, {"state", "sum"}
        )

    result = await get_instance(hass).async_add_executor_job(_fetch_existing)
    existing = result.get(statistic_id, []) if result else []
    _LOGGER.debug(
        "%s: %d new hourly buckets, %d existing rows in recorder",
        statistic_id,
        len(new_totals),
        len(existing),
    )

    baseline = 0.0
    merged = dict(new_totals)
    for row in existing:
        ts = row.get("start")
        if isinstance(ts, datetime):
            ts = ts.timestamp()
        if ts is None:
            continue
        if ts < earliest_new:
            row_sum = row.get("sum")
            if row_sum is not None:
                baseline = row_sum
            continue
        if ts not in merged:
            merged[ts] = (dt_util.utc_from_timestamp(ts), row.get("state") or 0.0)

    running = baseline
    stats = []
    for ts in sorted(merged):
        start, total = merged[ts]
        running += total
        stats.append(StatisticData(start=start, state=total, sum=running))
    return stats


def _hourly_buckets(points):
    unique = {}
    for when, value in points:
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        unique[when.timestamp()] = (when, value)
    buckets: dict = {}
    for when, value in unique.values():
        start = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(start, []).append(value)
    return dict(sorted(buckets.items()))
