import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.config_entry_flow import WebhookFlowHandler
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import DOMAIN, OPTION_MEDICATION_MERGES


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


class HaeIngestOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            options = dict(self.config_entry.options)
            options[OPTION_MEDICATION_MERGES] = user_input.get(
                OPTION_MEDICATION_MERGES, ""
            )
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPTION_MEDICATION_MERGES,
                        default=self.config_entry.options.get(
                            OPTION_MEDICATION_MERGES, ""
                        ),
                    ): TextSelector(TextSelectorConfig(multiline=True)),
                }
            ),
        )
