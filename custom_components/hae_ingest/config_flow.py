import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DATA_TYPE,
    DATA_TYPES,
    DOMAIN,
    OPTION_MEDICATION_MERGES,
    OPTION_SENSORS,
)
from .parser import parse_merge_rules, slugify


class HaeIngestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        configured = {
            entry.data.get(CONF_DATA_TYPE) for entry in self._async_current_entries()
        }
        available = {k: v for k, v in DATA_TYPES.items() if k not in configured}
        if not available:
            return self.async_abort(reason="all_configured")
        if user_input is not None:
            data_type = user_input[CONF_DATA_TYPE]
            await self.async_set_unique_id(data_type)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=DATA_TYPES.get(data_type, data_type),
                data={
                    CONF_DATA_TYPE: data_type,
                    CONF_WEBHOOK_ID: webhook.async_generate_id(),
                },
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_DATA_TYPE): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=k, label=v)
                            for k, v in available.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

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
        if self.config_entry.data.get(CONF_DATA_TYPE) not in (None, "medications"):
            return self.async_abort(reason="no_options")
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
