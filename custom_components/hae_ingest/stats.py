from __future__ import annotations

import logging
from datetime import timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SUM_UNITS = {"count", "kcal", "kJ", "km", "mi", "m", "ft", "yd", "L", "mL", "IU", "g", "mg", "µg"}


async def async_import_metric_statistics(hass: HomeAssistant, series_list) -> None:
    if not series_list or "recorder" not in hass.config.components:
        return
    for series in series_list:
        try:
            await _import_series(hass, series)
        except Exception:
            _LOGGER.exception("Failed to import statistics for %s", series["key"])


async def _import_series(hass: HomeAssistant, series) -> None:
    buckets = _hourly_buckets(series["points"])
    if not buckets:
        return
    statistic_id = f"{DOMAIN}:{series['key']}"
    is_sum = series.get("unit") in SUM_UNITS
    metadata = StatisticMetaData(
        source=DOMAIN,
        statistic_id=statistic_id,
        name=series["name"],
        unit_of_measurement=series.get("unit"),
        has_mean=not is_sum,
        has_sum=is_sum,
    )
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
    if stats:
        async_add_external_statistics(hass, metadata, stats)


async def _sum_stats(hass: HomeAssistant, statistic_id: str, buckets) -> list[StatisticData]:
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    running = 0.0
    last_start_ts = None
    rows = last.get(statistic_id) if last else None
    if rows:
        running = rows[0].get("sum") or 0.0
        last_start_ts = rows[0].get("start")
    stats = []
    for start, values in buckets.items():
        if last_start_ts is not None and start.timestamp() <= last_start_ts:
            continue
        total = sum(values)
        running += total
        stats.append(StatisticData(start=start, state=total, sum=running))
    return stats


def _hourly_buckets(points):
    buckets: dict = {}
    for when, value in points:
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        start = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(start, []).append(value)
    return dict(sorted(buckets.items()))
