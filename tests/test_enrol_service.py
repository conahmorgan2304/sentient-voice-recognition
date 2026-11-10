"""The services Sentient uses to start a speaker recording."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.sentient_voice_recognition import async_setup
from custom_components.sentient_voice_recognition.const import DOMAIN
from custom_components.sentient_voice_recognition.satellite import SENTENCES


@pytest.fixture
async def services(hass):
    runtime = SimpleNamespace(
        start_satellite_enrollment=lambda name: SENTENCES[0],
        cancel_satellite_enrollment=lambda: None)
    entry = SimpleNamespace(runtime_data=runtime)
    with patch("custom_components.sentient_voice_recognition.async_setup_recorder"):
        await async_setup(hass, {})
    return entry


async def test_start_returns_the_first_sentence(hass, services):
    with patch.object(type(hass.config_entries), "async_loaded_entries", return_value=[services]):
        out = await hass.services.async_call(DOMAIN, "start_speaker_enrolment", {"name": "Alex"}, blocking=True, return_response=True)
    assert out == {"sentence": SENTENCES[0], "minutes": 10}


async def test_start_when_not_running_is_a_clear_error(hass, services):
    with patch.object(type(hass.config_entries), "async_loaded_entries", return_value=[]):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, "start_speaker_enrolment", {"name": "Alex"}, blocking=True, return_response=True)


async def test_status_reports_engine_profiles_and_threshold(hass, services):
    async def ok():
        return None

    services.runtime_data.engine = SimpleNamespace(async_check=ok)
    services.runtime_data.distinctness = lambda: [{"name": "Alex", "nearest": None, "nearest_score": None}]
    services.options = {"match_threshold": 0.6}
    with patch.object(type(hass.config_entries), "async_loaded_entries", return_value=[services]):
        out = await hass.services.async_call(DOMAIN, "status", {}, blocking=True, return_response=True)
    assert out == {"engine": True, "profiles": [{"name": "Alex", "nearest": None, "nearest_score": None}], "threshold": 0.6}


async def test_status_says_the_engine_is_down_when_the_check_fails(hass, services):
    async def down():
        raise ValueError("no")

    services.runtime_data.engine = SimpleNamespace(async_check=down)
    services.runtime_data.distinctness = lambda: []
    services.options = {}
    with patch.object(type(hass.config_entries), "async_loaded_entries", return_value=[services]):
        out = await hass.services.async_call(DOMAIN, "status", {}, blocking=True, return_response=True)
    assert out["engine"] is False and out["threshold"] == 0.65


async def test_threshold_is_limited_to_the_supported_range(hass, services):
    import voluptuous as vol
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "set_match_threshold", {"value": 0.2}, blocking=True)
