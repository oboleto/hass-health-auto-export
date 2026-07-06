# Health Auto Export for Home Assistant

Custom Home Assistant integration that ingests JSON payloads sent by the [Health Auto Export](https://healthyapps.dev/) iOS app via its **REST API automations**. Unlike the app's built-in Home Assistant automation (which only supports Health Metrics), this integration accepts **all data types**:

| Data type | Result in Home Assistant |
|---|---|
| Health Metrics | One sensor per metric (`sensor.health_auto_export_step_count`, ...) + full history backfilled into long-term statistics |
| Workouts | `hae_ingest_workout` events + `last_workout` + one sensor per workout type (`workout_running`, ...) |
| Symptoms | `hae_ingest_symptom` events + `last_symptom` + one sensor per symptom (state = severity) |
| ECG | `hae_ingest_ecg` events + `sensor.health_auto_export_last_ecg` |
| Heart Rate Notifications | `hae_ingest_heart_rate_notification` events + last sensor |
| State of Mind | `hae_ingest_state_of_mind` events + last sensor |
| Cycle Tracking | `hae_ingest_cycle_tracking` events + one sensor per entry type (state = value) |
| Medications | `hae_ingest_medication` events + `last_medication` + one sensor per medication (state = Taken/Skipped/...) |

Metric sensors keep their unit of measurement, use `state_class: measurement` (so they show up in history graphs and long-term statistics), and survive Home Assistant restarts. Special formats are handled:

- `blood_pressure` → separate systolic/diastolic sensors
- `heart_rate` (Min/Avg/Max) → state is the average, min/max as attributes
- `sleep_analysis` (aggregated) → separate sensors for total, core, deep, REM, awake and in-bed hours
- Extra fields (`mealTime`, `reason`, `value`, `source`, ...) are exposed as attributes

Large arrays (GPS routes, ECG voltage measurements, per-second heart rate) are stripped from events and attributes to keep the recorder database healthy.

### Long-term statistics backfill

Every numeric data point in a Health Metrics payload (not just the latest) is imported into Home Assistant's long-term statistics with its original timestamp, under external statistic IDs like `hae_ingest:step_count`. Cumulative units (count, kcal, km, ...) are stored as hourly sums; everything else as hourly mean/min/max. Use them in statistics graph cards or the energy-style charts — history shows up even for past days.

## Installation

### HACS (recommended)

1. HACS → menu (⋮) → **Custom repositories**
2. Add this repository URL with category **Integration**
3. Install **Health Auto Export** and restart Home Assistant

### Manual

Copy `custom_components/hae_ingest` into your `config/custom_components/` folder and restart.

## Configuration

1. **Settings → Devices & Services → Add Integration → Health Auto Export**
2. Confirm; the flow displays a **webhook URL**. Copy it.
3. In the Health Auto Export app, create a **REST API** automation (not the "Home Assistant" type) for each data type you want:
   - **URL**: the webhook URL from step 2
   - **Export Format**: JSON
   - **Data Type**: Health Metrics, Workouts, Symptoms, etc.
   - Enable **Batch Requests** for large exports
4. Use **Manual Export** in the app to test, then check **Developer Tools → States**.

All automations can point to the same webhook URL — payloads are routed by content.

## Using the data

```yaml
automation:
  - alias: "Workout finished"
    trigger:
      - platform: event
        event_type: hae_ingest_workout
    action:
      - service: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.name }} — {{ (trigger.event.data.duration / 60) | round }} min"

  - alias: "Slept badly"
    trigger:
      - platform: numeric_state
        entity_id: sensor.health_auto_export_sleep_total
        below: 6
    action:
      - service: notify.mobile_app_phone
        data:
          message: "Only {{ states('sensor.health_auto_export_sleep_total') }}h of sleep."
```

## Notes

- The webhook accepts POSTs without a bearer token; the webhook ID itself is the secret. Prefer HTTPS or LAN-only exposure.
- iOS only runs automations while the phone is unlocked/charging; see the app's [REST API guide](https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/) for reliability tips.
