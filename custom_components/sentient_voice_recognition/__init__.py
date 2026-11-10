"""Home Assistant integration for local speaker identification."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_ENGINE_INSTALL, CONF_TOKEN
from .recognition import EngineClient, engine_url
from .runtime import Runtime, profile_store

type VoiceRecognitionEntry = ConfigEntry[Runtime]
PLATFORMS = (Platform.STT, Platform.CONVERSATION)


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VoiceRecognitionEntry) -> bool:
    """Unload both adapters."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: VoiceRecognitionEntry) -> None:
    """Delete stored voice profiles when the integration is removed."""
    await profile_store(hass, entry.entry_id).async_remove()
