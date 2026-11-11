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
