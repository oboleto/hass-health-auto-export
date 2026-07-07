from __future__ import annotations

from datetime import datetime

COLLECTIONS = {
    "workouts": "workout",
    "symptoms": "symptom",
    "ecg": "ecg",
    "heartRateNotifications": "heart_rate_notification",
    "stateOfMind": "state_of_mind",
    "cycleTracking": "cycle_tracking",
    "medications": "medication",
}

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S.%f %z",
    "%Y-%m-%d %I:%M:%S %p %z",
    "%Y-%m-%d %I:%M %p %z",
    "%Y-%m-%d",
)

UNIT_MAP = {"degC": "°C", "degF": "°F"}

SLEEP_FIELDS = (
    ("totalSleep", "sleep_total"),
    ("asleep", "sleep_asleep"),
    ("core", "sleep_core"),
    ("deep", "sleep_deep"),
    ("rem", "sleep_rem"),
    ("awake", "sleep_awake"),
    ("inBed", "sleep_in_bed"),
)

STATE_FIELDS = {
    "workouts": "name",
    "symptoms": "name",
    "ecg": "classification",
    "stateOfMind": "valence",
    "medications": "displayText",
}

PER_NAME = {
    "workouts": ("name", None),
    "symptoms": ("name", "severity"),
    "medications": ("displayText", "status"),
    "cycleTracking": ("name", "value"),
}

ITEM_SERIES = {
    "medications": {
        "name_field": "displayText",
        "date_fields": ("date", "scheduledDate"),
        "only_status": "taken",
        "metrics": [
            {"suffix": "doses", "label": "doses", "unit": "count", "amount": "dosage"},
        ],
    },
    "workouts": {
        "name_field": "name",
        "date_fields": ("start", "date", "end"),
        "metrics": [
            {"suffix": "sessions", "label": "sessions", "unit": "count"},
            {"suffix": "duration", "label": "duration", "unit": "min", "value": "duration", "scale": 1 / 60},
            {"suffix": "energy", "label": "active energy", "unit": "kcal", "value": "activeEnergyBurned"},
            {"suffix": "distance", "label": "distance", "unit": "km", "value": "distance"},
        ],
    },
    "symptoms": {
        "name_field": "name",
        "date_fields": ("start", "date", "end"),
        "metrics": [
            {"suffix": "occurrences", "label": "occurrences", "unit": "count"},
        ],
    },
}

NAME_OVERRIDES = {"last_ecg": "Last ECG"}

SKIP_ATTR_FIELDS = {"qty", "date", "startDate", "endDate"}


def parse_date(value):
    if not isinstance(value, str):
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def slugify(value):
    slug = "".join(c if c.isalnum() else "_" for c in str(value).lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _timestamp(item):
    for field in ("date", "scheduledDate", "end", "start", "startDate", "endDate"):
        parsed = parse_date(item.get(field))
        if parsed is not None:
            try:
                return parsed.timestamp()
            except (OverflowError, OSError, ValueError):
                return float("-inf")
    return float("-inf")


def _record(key, unit, value, attrs, state_class="measurement", device_class=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        state_class = None
    name = NAME_OVERRIDES.get(key, key.replace("_", " ").title())
    return {
        "key": key,
        "name": name,
        "unit": unit,
        "value": value,
        "attributes": attrs,
        "state_class": state_class,
        "device_class": device_class,
    }


def parse_metrics(metrics):
    records = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        points = [p for p in metric.get("data") or [] if isinstance(p, dict)]
        if not points:
            continue
        name = slugify(metric.get("name") or "unknown")
        unit = UNIT_MAP.get(metric.get("units"), metric.get("units"))
        point = max(points, key=_timestamp)
        records.extend(
            r for r in _metric_records(name, unit, point, len(points)) if r["value"] is not None
        )
    return records


def _metric_records(name, unit, point, sample_count):
    attrs = {
        "last_sample_date": point.get("date") or point.get("startDate"),
        "samples_in_payload": sample_count,
    }
    if "source" in point:
        attrs["source"] = point["source"]
    if name == "blood_pressure":
        return [
            _record(f"{name}_systolic", unit, point.get("systolic"), dict(attrs)),
            _record(f"{name}_diastolic", unit, point.get("diastolic"), dict(attrs)),
        ]
    if name == "sleep_analysis" and "qty" not in point:
        for field in ("sleepStart", "sleepEnd", "inBedStart", "inBedEnd"):
            if field in point:
                attrs[slugify(field)] = point[field]
        return [
            _record(key, unit, point[field], dict(attrs))
            for field, key in SLEEP_FIELDS
            if isinstance(point.get(field), (int, float))
        ]
    if any(field in point for field in ("Avg", "Min", "Max")):
        for field in ("Min", "Max"):
            if field in point:
                attrs[field.lower()] = point[field]
        value = point.get("Avg", point.get("Max", point.get("Min")))
        return [_record(name, unit, value, attrs)]
    for field, field_value in point.items():
        if field in SKIP_ATTR_FIELDS or field == "source":
            continue
        attrs[slugify(field)] = field_value
    value = point.get("qty")
    if value is None:
        numeric = [
            v for k, v in point.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        value = sum(numeric) if numeric else None
    return [_record(name, unit, value, attrs)]


def compact_item(item):
    out = {}
    for key, value in item.items():
        slug = slugify(key)
        if isinstance(value, list):
            if value and all(isinstance(v, str) for v in value):
                out[slug] = value
            continue
        if isinstance(value, dict):
            if "qty" in value:
                out[slug] = value.get("qty")
                if value.get("units"):
                    out[f"{slug}_units"] = value["units"]
            else:
                for sub in ("min", "avg", "max", "Min", "Avg", "Max"):
                    sub_value = value.get(sub)
                    if isinstance(sub_value, dict) and "qty" in sub_value:
                        out[f"{slug}_{sub.lower()}"] = sub_value["qty"]
                    elif isinstance(sub_value, (int, float)):
                        out[f"{slug}_{sub.lower()}"] = sub_value
            continue
        out[slug] = value
    return out


def parse_merge_rules(text):
    rules = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        source, _, target = line.partition("=")
        source = slugify(source.strip())
        target = slugify(target.strip())
        if source and target and source != target:
            rules[source] = target
    return rules


def _medication_extra(raw):
    extra = {}
    codings = raw.get("codings")
    if isinstance(codings, list):
        codes = [
            {"code": c.get("code"), "system": c.get("system")}
            for c in codings
            if isinstance(c, dict) and c.get("code")
        ]
        if codes:
            extra["medication_codes"] = codes
            rxnorm = next(
                (
                    c["code"]
                    for c in codes
                    if "rxnorm" in (c.get("system") or "").lower()
                ),
                None,
            )
            if rxnorm:
                extra["rxnorm_code"] = rxnorm
    archived = bool(raw.get("isArchived"))
    extra["archived"] = archived
    extra["active"] = not archived and not raw.get("end")
    return extra


def parse_collection(collection_key, items, merges=None):
    valid = [i for i in items if isinstance(i, dict)]
    if not valid:
        return [], []
    events = [compact_item(i) for i in valid]
    singular = COLLECTIONS[collection_key]
    order = sorted(range(len(valid)), key=lambda i: _timestamp(valid[i]))
    latest = events[order[-1]]
    state_field = STATE_FIELDS.get(collection_key)
    value = valid[order[-1]].get(state_field) if state_field else None
    if value is None:
        value = latest.get("start") or latest.get("date") or singular
    attrs = {**latest, "items_in_payload": len(events)}
    records = [_record(f"last_{singular}", None, value, attrs, state_class=None)]
    name_field, item_state_field = PER_NAME.get(collection_key, (None, None))
    if name_field:
        date_fields = ITEM_SERIES.get(collection_key, {}).get(
            "date_fields", ("date", "start", "end")
        )
        groups = {}
        logs = {}
        for idx in order:
            raw = valid[idx]
            item_name = raw.get(name_field) or raw.get("nickname")
            if not isinstance(item_name, str) or not item_name:
                continue
            state = raw.get(item_state_field) if item_state_field else None
            if state is None or state == "":
                state = raw.get("end") or raw.get("start") or raw.get("date")
            slug = slugify(item_name)
            if collection_key == "medications" and merges:
                slug = merges.get(slug, slug)
            groups[slug] = (item_name, state, events[idx], raw)
            log_entry = {
                "date": next(
                    (raw.get(f) for f in date_fields if raw.get(f)), None
                ),
                "status": raw.get("status"),
            }
            if _is_number(raw.get("dosage")):
                log_entry["dosage"] = raw["dosage"]
            logs.setdefault(slug, []).append(log_entry)
        for slug, (item_name, state, item_attrs, raw) in groups.items():
            extra = {"item_name": item_name}
            if collection_key == "medications":
                extra.update(_medication_extra(raw))
            if slug in logs:
                extra["recent_records"] = logs[slug][-20:]
            records.append(
                _record(
                    f"{singular}_{slug}",
                    None,
                    state,
                    {**item_attrs, **extra},
                    state_class=None,
                )
            )
            if slug in logs:
                if collection_key == "medications":
                    last_when = next(
                        (
                            r["date"]
                            for r in reversed(logs[slug])
                            if isinstance(r.get("status"), str)
                            and r["status"].lower() == "taken"
                        ),
                        None,
                    )
                else:
                    last_when = logs[slug][-1]["date"]
                parsed = parse_date(last_when)
                if parsed is not None and parsed.tzinfo is not None:
                    suffix = "last_dose" if collection_key == "medications" else "last"
                    records.append(
                        _record(
                            f"{singular}_{slug}_{suffix}",
                            None,
                            parsed.isoformat(),
                            {"item_name": item_name},
                            state_class=None,
                            device_class="timestamp",
                        )
                    )
    return events, [r for r in records if r["value"] is not None]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def metric_series(metrics):
    series = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name = slugify(metric.get("name") or "unknown")
        unit = UNIT_MAP.get(metric.get("units"), metric.get("units"))
        points = []
        for point in metric.get("data") or []:
            if not isinstance(point, dict):
                continue
            when = parse_date(point.get("date") or point.get("startDate"))
            if when is None:
                continue
            value = point.get("qty")
            if not _is_number(value):
                value = point.get("Avg")
            if not _is_number(value):
                value = point.get("totalSleep")
                if _is_number(value):
                    name = "sleep_total"
            if not _is_number(value):
                continue
            points.append((when, float(value)))
        if points:
            points.sort(key=lambda p: _safe_ts(p[0]))
            series.append(
                {
                    "key": name,
                    "name": name.replace("_", " ").title(),
                    "unit": unit,
                    "points": points,
                }
            )
    return series


def _numeric_value(raw):
    if _is_number(raw):
        return float(raw)
    if isinstance(raw, dict) and _is_number(raw.get("qty")):
        return float(raw["qty"])
    return None


def collection_series(collection_key, items, merges=None):
    config = ITEM_SERIES.get(collection_key)
    if not config:
        return []
    name_field = config["name_field"]
    only_status = config.get("only_status")
    groups = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if only_status:
            status = item.get("status")
            if isinstance(status, str) and status.lower() != only_status:
                continue
        name = item.get(name_field) or item.get("nickname") or item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        when = None
        for field in config["date_fields"]:
            when = parse_date(item.get(field))
            if when is not None:
                break
        if when is None:
            continue
        slug = slugify(name)
        if merges and collection_key == "medications":
            slug = merges.get(slug, slug)
        group = groups.setdefault(slug, {"name": name.strip(), "metrics": {}})
        for metric in config["metrics"]:
            if "value" in metric:
                amount = _numeric_value(item.get(metric["value"]))
                if amount is None:
                    continue
                amount *= metric.get("scale", 1)
            elif "amount" in metric:
                raw = item.get(metric["amount"])
                amount = float(raw) if _is_number(raw) and raw > 0 else 1.0
            else:
                amount = 1.0
            group["metrics"].setdefault(metric["suffix"], []).append((when, amount))
    series = []
    prefix = COLLECTIONS[collection_key]
    labels = {m["suffix"]: (m["label"], m["unit"]) for m in config["metrics"]}
    for slug, group in groups.items():
        for suffix, points in group["metrics"].items():
            points.sort(key=lambda p: _safe_ts(p[0]))
            label, unit = labels[suffix]
            series.append(
                {
                    "key": f"{prefix}_{slug}_{suffix}",
                    "name": f"{group['name']} ({label})",
                    "unit": unit,
                    "points": points,
                }
            )
    return series


def medication_series(items, merges=None):
    return collection_series("medications", items, merges)


def _safe_ts(when):
    try:
        return when.timestamp()
    except (OverflowError, OSError, ValueError):
        return float("-inf")
