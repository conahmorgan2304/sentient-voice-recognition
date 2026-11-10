"""Home Assistant integration for local speaker identification."""

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENGINE_INSTALL, CONF_TOKEN, RECORDER_PATH
from .recorder import async_register_recorder, async_setup_recorder
from .recognition import EngineClient, engine_url
from .runtime import Runtime, profile_store
from .speaker_context import SPEAKER_FOR_TURN, speaker_for_turn

type VoiceRecognitionEntry = ConfigEntry[Runtime]
PLATFORMS = (Platform.STT, Platform.CONVERSATION)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the recorder and the optional local speaker lookup."""
    await async_setup_recorder(hass)
    hass.data[SPEAKER_FOR_TURN] = speaker_for_turn
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VoiceRecognitionEntry) -> bool:
    """Load profiles and register the two optional Assist adapters."""
    engine = EngineClient(
        hass, entry.options[CONF_TOKEN], engine_url(entry.options.get(CONF_ENGINE_INSTALL))
    )
    runtime = Runtime(hass, entry.entry_id, engine)
    try:
        await runtime.async_load()
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise ConfigEntryError("Stored voice profiles could not be loaded") from err

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_recorder(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VoiceRecognitionEntry) -> bool:
    """Unload both adapters."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_close()
    frontend.async_remove_panel(hass, RECORDER_PATH)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: VoiceRecognitionEntry) -> None:
    """Delete stored voice profiles when the integration is removed."""
    await profile_store(hass, entry.entry_id).async_remove()
