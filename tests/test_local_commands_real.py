"""Local commands against Home Assistant's real sentence matcher, with and without a pipeline's chat log."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.setup import async_setup_component

from custom_components.sentient_voice_recognition.conversation import VoiceRecognitionAgent
from custom_components.sentient_voice_recognition.runtime import Runtime


async def _ask(hass, text):
    for domain in ("homeassistant", "conversation", "intent"):
        assert await async_setup_component(hass, domain, {})
    entry = SimpleNamespace(entry_id="e", options={"conversation_agent": "conversation.x", "local_commands": True},
                            runtime_data=Runtime(hass, "entry", SimpleNamespace()))
    agent = VoiceRecognitionAgent(entry)
    agent.hass, agent.entity_id = hass, "conversation.svr"
    request = conversation.ConversationInput(
        text=text, context=Context(), conversation_id=None, device_id=None, satellite_id=None,
        language="en", agent_id="conversation.svr")
    with patch.object(VoiceRecognitionAgent, "_agent", new_callable=PropertyMock, return_value=object()), \
            patch("homeassistant.components.conversation.async_converse", new=AsyncMock(return_value="AGENT")), \
            patch.object(agent, "async_write_ha_state"):
        return await agent.internal_async_process(request)


async def test_a_real_local_sentence_is_answered_without_a_pipeline_chat_log(hass):
    result = await _ask(hass, "what time is it")
    assert result != "AGENT"
    assert result.response.speech["plain"]["speech"] and result.conversation_id


async def test_a_real_question_home_assistant_cannot_match_goes_to_the_agent(hass):
    assert await _ask(hass, "what should I cook for six people tonight") == "AGENT"


async def test_the_chat_session_is_opened_with_the_requests_own_context(hass):
    """Home Assistant 2026.11 reports a chat session opened without a context; it stops working in 2027.5."""
    from contextlib import contextmanager

    from custom_components.sentient_voice_recognition import conversation as module
    from homeassistant.helpers.chat_session import async_get_chat_session as real

    seen = []

    @contextmanager
    def spy(hass_, conversation_id=None, *, context=None):
        seen.append(context)
        with real(hass_, conversation_id) as session:
            yield session

    with patch.object(module, "async_get_chat_session", spy), patch.object(module, "_SESSION_TAKES_CONTEXT", True):
        result = await _ask(hass, "what time is it")
    assert result != "AGENT" and len(seen) == 1 and isinstance(seen[0], Context)


def test_older_home_assistant_without_the_context_argument_still_works():
    from custom_components.sentient_voice_recognition import conversation as module

    with patch.object(module, "_SESSION_TAKES_CONTEXT", False):
        assert module._session_context(SimpleNamespace(context=Context())) == {}
