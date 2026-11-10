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
