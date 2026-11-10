"""Host the admin-only browser enrollment panel."""

from base64 import b64decode
from binascii import Error as Base64Error
from pathlib import Path

import probatio

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN, ENROLLMENT_PROMPTS, RECORDER_PATH, RECORDER_SECONDS, SAMPLE_RATE
from .runtime import ProfileError

_ASSET_PATH = f"/{RECORDER_PATH}-assets"
_MAX_RECORDING = 44 + SAMPLE_RATE * 2 * RECORDER_SECONDS
_MAX_ENCODED = 4 * ((_MAX_RECORDING + 2) // 3)


async def async_setup_recorder(hass: HomeAssistant) -> None:
    """Register assets and the enrollment command once per Home Assistant run."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_ASSET_PATH, str(Path(__file__).with_name("frontend")), False)]
    )
    websocket_api.async_register_command(hass, websocket_enroll)


async def async_register_recorder(hass: HomeAssistant) -> None:
    """Make the recorder available while the integration is loaded."""
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=RECORDER_PATH,
        webcomponent_name="sentient-voice-enrollment",
        module_url=f"{_ASSET_PATH}/recorder.js?v=2",
        require_admin=True,
        config={"prompts": list(ENROLLMENT_PROMPTS), "seconds": RECORDER_SECONDS},
    )


@websocket_api.websocket_command({
    "type": f"{DOMAIN}/enroll_recordings",
    probatio.Required("name"): probatio.All(str, probatio.Length(max=128)),
    probatio.Required("consent"): bool,
    probatio.Required("recordings"): probatio.All(
        [probatio.All(str, probatio.Length(max=_MAX_ENCODED))],
        probatio.Length(min=3, max=3),
    ),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_enroll(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Enroll three browser recordings without staging audio files on disk."""
    message_id = msg["id"]
    if not msg["consent"]:
        connection.send_result(message_id, {"error": "consent_required"})
        return

    entry = next(
        (entry for entry in hass.config_entries.async_entries(DOMAIN)
         if entry.state is ConfigEntryState.LOADED),
        None,
    )
    if entry is None:
        connection.send_result(message_id, {"error": "integration_reloaded"})
        return

    try:
        recordings = []
        for index, encoded in enumerate(msg["recordings"]):
            try:
                wav = b64decode(encoded, validate=True)
            except (Base64Error, ValueError) as err:
                raise ProfileError("sample_unreadable", index) from err
            if not 44 < len(wav) <= _MAX_RECORDING:
                raise ProfileError("sample_unreadable", index)
            recordings.append(wav)
        await entry.runtime_data.async_enroll(msg["name"], recordings=tuple(recordings))
    except ProfileError as err:
        connection.send_result(message_id, {"error": str(err), "sample_index": err.sample_index})
    except OSError:
        connection.send_result(message_id, {"error": "storage_error"})
    else:
        connection.send_result(message_id, {})
