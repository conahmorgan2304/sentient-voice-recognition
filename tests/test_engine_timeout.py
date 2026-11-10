"""A live Assist turn gives the engine a little longer; uploads keep their longer wait."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.sentient_voice_recognition.const import (
    ENGINE_DIMENSION, LIVE_EMBED_TIMEOUT, UPLOAD_EMBED_TIMEOUT)
from custom_components.sentient_voice_recognition.recognition import EngineClient


class _Reply:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return {"embedding": [0.0] * ENGINE_DIMENSION}


async def _timeout_used(hass, wav):
    seen = {}
    client = EngineClient(hass, "token")

    def post(url, **kwargs):
        seen["total"] = kwargs["timeout"].total
        return _Reply()

    client._session = SimpleNamespace(post=post)
    try:
        await client.async_embed(b"\0" * 10, wav=wav)
    except Exception:  # the reply shape is not what this test is about
        pass
    return seen["total"]


async def test_live_turns_wait_longer_than_before_but_uploads_wait_longest(hass):
    live = await _timeout_used(hass, wav=False)
    upload = await _timeout_used(hass, wav=True)
    assert live == LIVE_EMBED_TIMEOUT == 1.5 and upload == UPLOAD_EMBED_TIMEOUT and live < upload
