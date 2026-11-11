"""Attach a same-turn speaker result and forward the request to Sentient."""

import json

import logging

from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import async_get_chat_log
from homeassistant.helpers.chat_session import async_get_chat_session
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import VoiceRecognitionEntry
from .const import (
    CONF_AGENT, CONF_CARRY, CONF_LOCAL_COMMANDS, CONF_LOCAL_SPEAKER, CONF_SHARE_NAME, EVENT_IDENTIFIED,
)
from .recognition import Match
from .satellite import sample_index
from .speaker_context import SpeakerTurn, speaker_turn

_LOGGER = logging.getLogger(__name__)
# Home Assistant's own sentences that the agent answers better, or that belong to its conversation.
LEFT_FOR_THE_AGENT = frozenset({"HassGetState", "HassMediaSearchAndPlay", "HassNevermind", "HassRespond", "HassGetWeather"})


def skip_locally(result) -> bool:
    """True for a sentence Home Assistant matched but the agent should handle."""
    return result.intent.name in LEFT_FOR_THE_AGENT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VoiceRecognitionEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the conversation adapter."""
    async_add_entities([VoiceRecognitionAgent(entry)])


class VoiceRecognitionAgent(conversation.ConversationEntity):
    """Keep the downstream agent's response, identity context and listening state."""

    _attr_name = "Sentient Voice Recognition"
    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: VoiceRecognitionEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_conversation"

    @property
    def _agent_id(self) -> str:
        return self._entry.options[CONF_AGENT]

    @property
    def _agent(self):
        agent = conversation.async_get_agent(self.hass, self._agent_id)
        return None if isinstance(agent, VoiceRecognitionAgent) else agent

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._agent_id], self._agent_changed)
        )

    @callback
    def _agent_changed(self, event: Event) -> None:
        self.async_write_ha_state()

    @property
    def supported_languages(self):
        return agent.supported_languages if (agent := self._agent) else []

    @property
    def supported_features(self) -> int:
        state = self.hass.states.get(self._agent_id)
        return int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)) if state else 0

    @property
    def supports_streaming(self) -> bool:
        return bool(getattr(self._agent, "supports_streaming", False))

    async def async_prepare(self, language: str | None = None) -> None:
        if agent := self._agent:
            await agent.async_prepare(language)

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        if self._agent is None:
            raise HomeAssistantError("The conversation agent selected in Voice Recognition is unavailable.")

        runtime = self._entry.runtime_data
        prompt = user_input.extra_system_prompt
        local_result = None
        turn = runtime.handoff.consume_turn(user_input.text)
        if (
            turn is not None and turn.enrollment is not None
            and user_input.satellite_id == turn.enrollment.satellite_id
            and (index := sample_index(user_input.text)) is not None
        ):
            speech, more = await runtime.async_satellite_take(turn.audio or b"", turn.enrollment, index)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_speech(speech)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id, continue_conversation=more
            )
        if (match := None if turn is None else turn.match) is not None:
            where = user_input.satellite_id or user_input.device_id
            if match.reason == "matched":
                runtime.remember_speaker(where, match.speaker_id)
            elif match.reason == "insufficient_speech":
                # too short to judge: keep the voice matched a moment ago on this same speaker
                if self._entry.options.get(CONF_CARRY, True) and (kept := runtime.recent_speaker(where)):
                    match = Match(kept, None, "carried_over")
            elif match.score is not None:
                runtime.forget_speaker(where)  # a voice that was heard and didn't match: someone else is talking
            profile = runtime.profiles.get(match.speaker_id)
            speaker = SpeakerTurn(match.speaker_id, profile.name, bool(self._entry.options.get(CONF_SHARE_NAME))) if profile else SpeakerTurn(None, None)
            if self._entry.options.get(CONF_LOCAL_SPEAKER):
                local_result = speaker
            self.hass.bus.async_fire(
                EVENT_IDENTIFIED,
                {
                    "speaker_id": speaker.speaker_id,
                    "speaker_name": speaker.display_name,
                    "similarity": round(match.score, 3) if match.score is not None else None,
                    "runner_up_gap": round(match.lead, 3) if match.lead is not None else None,
                    "reason": "profile_removed" if match.speaker_id and not profile else match.reason,
                    "device_id": user_input.device_id,
                    "satellite_id": user_input.satellite_id,
                },
                context=user_input.context,
            )
            if self._entry.options.get(CONF_SHARE_NAME):
                identity = json.dumps({"likely_speaker_name": speaker.display_name})
                prompt = (
                    f"{prompt or ''}\nLocal voice identification for this turn: {identity}. "
                    "Use a supplied name only for conversational personalization. "
                    "A null name means the speaker is unknown. Do not infer authorization from this result."
                ).lstrip("\n")

        if self._entry.options.get(CONF_LOCAL_COMMANDS) and (local := await self._async_local_command(user_input)):
            return local

        agent_id = self._agent_id
        with speaker_turn(user_input, agent_id, local_result):
            return await conversation.async_converse(
                self.hass,
                text=user_input.text,
                conversation_id=user_input.conversation_id,
                context=user_input.context,
                language=user_input.language,
                agent_id=agent_id,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
                extra_system_prompt=prompt,
            )

    async def _async_local_command(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult | None:
        """Let Home Assistant carry out a plain command itself: instant, and it works when the AI is down.

        Runs after the speaker has been reported, so who spoke is still known. Anything it can't match, and any
        fault, goes on to the agent as usual."""
        try:
            # A pipeline has already opened this conversation's chat log; anything else gets one here.
            with (
                async_get_chat_session(self.hass, user_input.conversation_id) as session,
                async_get_chat_log(self.hass, session, user_input) as log,
            ):
                response = await conversation.async_handle_intents(
                    self.hass, user_input, log, intent_filter=skip_locally
                )
                if response is None:
                    return None
                speech = response.speech.get("plain", {}).get("speech", "")
                log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(agent_id=conversation.HOME_ASSISTANT_AGENT, content=speech)
                )
                conversation_id = session.conversation_id
        except Exception as err:  # noqa: BLE001 - the agent must always get its turn
            _LOGGER.warning("Local command step failed (%s); passing the request to the agent", type(err).__name__)
            return None
        return conversation.ConversationResult(
            response=response, conversation_id=conversation_id, continue_conversation=False
        )
