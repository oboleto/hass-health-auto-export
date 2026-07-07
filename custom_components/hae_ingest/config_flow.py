import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.config_entry_flow import WebhookFlowHandler
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DOMAIN, OPTION_MEDICATION_MERGES, OPTION_SENSORS
from .parser import parse_merge_rules, slugify


@config_entries.HANDLERS.register(DOMAIN)
class HaeIngestFlowHandler(WebhookFlowHandler):
    def __init__(self) -> None:
        super().__init__(
            DOMAIN,
            "Health Auto Export Ingest",
            {"docs_url": "https://github.com/oboleto/hass-health-auto-export"},
            False,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HaeIngestOptionsFlow()


def current_merge_rules(options) -> dict:
    raw = options.get(OPTION_MEDICATION_MERGES)
    if isinstance(raw, dict):
        return {
            slugify(k): slugify(v)
            for k, v in raw.items()
            if slugify(k) and slugify(v) and slugify(k) != slugify(v)
        }
    return parse_merge_rules(raw)


class HaeIngestOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        options = dict(self.config_entry.options)
        rules = current_merge_rules(options)
        names = self._medication_names()
        if user_input is not None:
            for source in user_input.get("remove") or []:
                rules.pop(source, None)
            source = slugify(user_input.get("source") or "")
            target = slugify(user_input.get("target") or "")
            if source and target and source != target:
                rules[source] = target
            options[OPTION_MEDICATION_MERGES] = rules
            return self.async_create_entry(data=options)

        def _label(slug):
            return names.get(slug) or slug.replace("_", " ").title()

        med_options = [
            SelectOptionDict(value=slug, label=label)
            for slug, label in sorted(names.items(), key=lambda kv: kv[1].lower())
        ]
        schema: dict = {}
        if rules:
            schema[vol.Optional("remove")] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=source, label=f"{_label(source)} \u2192 {_label(target)}"
                        )
                        for source, target in sorted(rules.items())
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        med_selector = SelectSelector(
            SelectSelectorConfig(
                options=med_options,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        schema[vol.Optional("source")] = med_selector
        schema[vol.Optional("target")] = med_selector
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    def _medication_names(self) -> dict:
        names: dict = {}
        for meta in self.config_entry.options.get(OPTION_SENSORS, []):
            if not isinstance(meta, dict):
                continue
            key = meta.get("key") or ""
            if not key.startswith("medication_") or key.endswith("_last_dose"):
                continue
            slug = key[len("medication_"):]
            names[slug] = meta.get("device_name") or meta.get("name") or slug
        for source, target in current_merge_rules(self.config_entry.options).items():
            names.setdefault(source, source.replace("_", " ").title())
            names.setdefault(target, target.replace("_", " ").title())
        return names
