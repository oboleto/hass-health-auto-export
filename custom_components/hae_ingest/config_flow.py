from homeassistant.helpers import config_entry_flow

from .const import DOMAIN

config_entry_flow.register_webhook_flow(
    DOMAIN,
    "Health Auto Export Ingest",
    {"docs_url": "https://github.com/oboleto/hass-health-auto-export"},
)
