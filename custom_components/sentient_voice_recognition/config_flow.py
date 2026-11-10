"""Configure backing providers and enroll voice profiles."""

from typing import Any

import aiohttp

from homeassistant.components import conversation, stt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from .const import (
    CONF_AGENT,
    CONF_ENGINE_INSTALL,
    CONF_SHARE_NAME,
    CONF_STT,
    CONF_THRESHOLD,
    CONF_TOKEN,
    DEFAULT_THRESHOLD,
    DOMAIN,
    ENGINE_INSTALL_LOCAL,
    ENGINE_INSTALL_REPOSITORY,
    TITLE,
)
from .recognition import EngineClient, engine_url, valid_token
from .runtime import ProfileError

_SAMPLE_FIELDS = ("sample_one", "sample_two", "sample_three")
_REPORT_ONLY = "report_only"
_EXPECT_UNKNOWN = "expect_unknown"


def _wav_selector() -> FileSelector:
    return FileSelector(FileSelectorConfig(accept=".wav,audio/wav,audio/x-wav"))


def _choices(hass: HomeAssistant, domain: str, current: str | None):
    registry = entity_registry.async_get(hass)
    candidates = set(hass.states.async_entity_ids(domain))
    if domain == "stt":
        for provider in ("cloud", stt.async_default_engine(hass)):
            if provider and stt.async_get_speech_to_text_engine(hass, provider):
                candidates.add(provider)
    if current:
        candidates.add(current)

    choices = []
    for entity_id in sorted(candidates):
        if (entity := registry.async_get(entity_id)) and entity.platform == DOMAIN:
            continue
        state = hass.states.get(entity_id)
        label = f"{state.name} ({entity_id})" if state else entity_id
        choices.append({"value": entity_id, "label": label})
    return SelectSelector(SelectSelectorConfig(options=choices))


def _settings(
    flow: ConfigFlow | OptionsFlowWithReload,
    values: dict[str, Any],
    *,
    reconfigure: bool = False,
) -> probatio.Schema:
    fields = {
        probatio.Required(CONF_STT): _choices(flow.hass, "stt", values.get(CONF_STT)),
        probatio.Required(CONF_AGENT): _choices(flow.hass, "conversation", values.get(CONF_AGENT)),
        probatio.Required(CONF_THRESHOLD, default=DEFAULT_THRESHOLD): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0.5, max=0.9)
        ),
        probatio.Required(CONF_SHARE_NAME, default=False): bool,
    }
    default_install = ENGINE_INSTALL_LOCAL if reconfigure else ENGINE_INSTALL_REPOSITORY
    fields[probatio.Required(
        CONF_ENGINE_INSTALL, default=values.get(CONF_ENGINE_INSTALL, default_install)
    )] = SelectSelector(SelectSelectorConfig(
        options=[ENGINE_INSTALL_REPOSITORY, ENGINE_INSTALL_LOCAL],
        translation_key=CONF_ENGINE_INSTALL,
    ))
    token_selector = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
    token_field = probatio.Optional if reconfigure else probatio.Required
    fields[token_field(CONF_TOKEN)] = token_selector
    suggested = {key: value for key, value in values.items() if key != CONF_TOKEN}
    return flow.add_suggested_values_to_schema(probatio.Schema(fields), suggested)


async def _provider_errors(hass: HomeAssistant, values: dict[str, Any], *, check_engine: bool = True) -> dict[str, str]:
    errors = {}
    if stt.async_get_speech_to_text_engine(hass, values[CONF_STT]) is None:
        errors[CONF_STT] = "provider_unavailable"
    if conversation.async_get_agent(hass, values[CONF_AGENT]) is None:
        errors[CONF_AGENT] = "provider_unavailable"
    registry = entity_registry.async_get(hass)
    for field in (CONF_STT, CONF_AGENT):
        if (entry := registry.async_get(values[field])) and entry.platform == DOMAIN:
            errors[field] = "recursive_provider"
    if not valid_token(values[CONF_TOKEN]):
        errors[CONF_TOKEN] = "invalid_token"
    elif not errors and check_engine:
        try:
            await EngineClient(
                hass, values[CONF_TOKEN], engine_url(values.get(CONF_ENGINE_INSTALL))
            ).async_check()
        except (TimeoutError, aiohttp.ClientError, ValueError):
            errors["base"] = "engine_unavailable"
    return errors


class VoiceRecognitionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a single Voice Recognition instance."""

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            errors = await _provider_errors(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=TITLE, data={}, options=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_settings(self, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        return VoiceRecognitionOptions()


class VoiceRecognitionOptions(OptionsFlowWithReload):
    """Change providers and manage voice profiles."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        menu = ["settings"]
        if self.config_entry.state is ConfigEntryState.LOADED:
            menu.append("profiles")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_profiles(self, user_input=None) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        menu = ["enroll"]
        profiles = self.config_entry.runtime_data.profiles
        if profiles:
            menu.extend(("test", "rename", "retrain", "delete"))
        return self.async_show_menu(
            step_id="profiles",
            menu_options=menu,
            description_placeholders={"count": str(len(profiles))},
        )

    def _profile_selector(self, *, for_test: bool = False) -> SelectSelector:
        profiles = self.config_entry.runtime_data.profiles
        choices = [
            {"value": key, "label": profile.name}
            for key, profile in sorted(profiles.items(), key=lambda item: item[1].name.casefold())
        ]
        if for_test:
            choices = [
                {"value": _REPORT_ONLY, "label": "Show the result only"},
                {"value": _EXPECT_UNKNOWN, "label": "Unknown speaker"},
                *choices,
            ]
        return SelectSelector(SelectSelectorConfig(options=choices))

    async def async_step_settings(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            token = user_input.get(CONF_TOKEN) or self.config_entry.options[CONF_TOKEN]
            values = {**user_input, CONF_TOKEN: token}
            errors = await _provider_errors(
                self.hass,
                values,
                check_engine=(
                    token != self.config_entry.options[CONF_TOKEN]
                    or values[CONF_ENGINE_INSTALL]
                    != self.config_entry.options.get(CONF_ENGINE_INSTALL, ENGINE_INSTALL_LOCAL)
                ),
            )
            if not errors:
                return self.async_create_entry(data=values)
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings(
                self, user_input or dict(self.config_entry.options), reconfigure=True
            ),
            errors=errors,
        )

    async def async_step_enroll(self, user_input=None) -> ConfigFlowResult:
        return await self._async_enrollment_form("enroll", user_input)

    async def async_step_retrain(self, user_input=None) -> ConfigFlowResult:
        return await self._async_enrollment_form("retrain", user_input)

    async def _async_enrollment_form(self, step_id: str, user_input) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")

        retrain = step_id == "retrain"
        if retrain and not self.config_entry.runtime_data.profiles:
            return self.async_abort(reason="no_profiles")
        errors = {}
        if user_input is not None and not user_input["consent"]:
            errors["base"] = "consent_required"
        elif user_input is not None:
            try:
                await self.config_entry.runtime_data.async_enroll(
                    user_input.get("name", ""),
                    tuple(user_input[field] for field in _SAMPLE_FIELDS),
                    speaker_id=user_input.get("profile") if retrain else None,
                )
            except ProfileError as err:
                field = _SAMPLE_FIELDS[err.sample_index] if err.sample_index is not None else "base"
                errors[field] = str(err)
            except OSError:
                errors["base"] = "storage_error"
            else:
                return self.async_abort(reason="profile_retrained" if retrain else "profile_enrolled")

        person_field = (
            {probatio.Required("profile"): self._profile_selector()}
            if retrain else {probatio.Required("name"): str}
        )
        schema = probatio.Schema(
            {
                **person_field,
                **{probatio.Required(field): _wav_selector() for field in _SAMPLE_FIELDS},
                probatio.Required("consent", default=False): bool,
            }
        )
        suggested = {
            key: value for key, value in (user_input or {}).items() if key in ("name", "profile")
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_rename(self, user_input=None) -> ConfigFlowResult:
        return await self._async_edit_profile("rename", user_input)

    async def async_step_delete(self, user_input=None) -> ConfigFlowResult:
        return await self._async_edit_profile("delete", user_input)

    async def _async_edit_profile(self, step_id: str, user_input) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")

        runtime = self.config_entry.runtime_data
        if not runtime.profiles:
            return self.async_abort(reason="no_profiles")
        deleting = step_id == "delete"
        errors = {}
        if user_input is not None and deleting and not user_input["confirm"]:
            errors["base"] = "confirm_delete"
        elif user_input is not None:
            try:
                if deleting:
                    await runtime.async_delete(user_input["profile"])
                else:
                    await runtime.async_rename(user_input["profile"], user_input["name"])
            except ProfileError as err:
                errors["base"] = str(err)
            except OSError:
                errors["base"] = "storage_error"
            else:
                return self.async_abort(reason="profile_deleted" if deleting else "profile_renamed")

        schema = {probatio.Required("profile"): self._profile_selector()}
        if deleting:
            schema[probatio.Required("confirm", default=False)] = bool
        else:
            schema[probatio.Required("name")] = str
        return self.async_show_form(
            step_id=step_id, data_schema=probatio.Schema(schema), errors=errors
        )

    async def async_step_test(self, user_input=None) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")

        runtime = self.config_entry.runtime_data
        if not runtime.profiles:
            return self.async_abort(reason="no_profiles")
        errors = {}
        threshold = self.config_entry.options.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        if user_input is not None:
            expected = user_input["expected"]
            expected_profile = runtime.profiles.get(expected)
            if expected not in (_REPORT_ONLY, _EXPECT_UNKNOWN) and expected_profile is None:
                errors["base"] = "profile_missing"
            else:
                try:
                    match = await runtime.async_test(user_input["sample"], threshold)
                except ProfileError as err:
                    errors["base"] = str(err)
                else:
                    matched_profile = runtime.profiles.get(match.speaker_id)
                    result = "test_complete"
                    if expected != _REPORT_ONLY:
                        expected_id = None if expected == _EXPECT_UNKNOWN else expected
                        result = "test_passed" if expected_id == match.speaker_id else "test_failed"
                    return self.async_abort(
                        reason=result,
                        description_placeholders={
                            "expected": expected_profile.name if expected_profile else "Unknown",
                            "speaker": matched_profile.name if matched_profile else "Unknown",
                            "score": f"{match.score:.3f}" if match.score is not None else "—",
                            "lead": f"{match.lead:.3f}" if match.lead is not None else "—",
                            "threshold": f"{threshold:.2f}",
                            "decision": {
                                "matched": "Matched",
                                "below_threshold": "Below minimum similarity",
                                "ambiguous": "Too close to another profile",
                            }[match.reason],
                        },
                    )
        return self.async_show_form(
            step_id="test",
            data_schema=probatio.Schema({
                probatio.Required("sample"): _wav_selector(),
                probatio.Required("expected", default=_REPORT_ONLY): self._profile_selector(for_test=True),
            }),
            errors=errors,
        )
