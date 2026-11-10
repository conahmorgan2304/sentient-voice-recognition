"""Attach a same-turn speaker result and forward the request to Sentient."""

import json

from homeassistant.components import conversation
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import VoiceRecognitionEntry
from .const import CONF_AGENT, CONF_SHARE_NAME, EVENT_IDENTIFIED


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
        if (match := runtime.handoff.consume(user_input.text)) is not None:
            profile = runtime.profiles.get(match.speaker_id)
            self.hass.bus.async_fire(
                EVENT_IDENTIFIED,
                {
                    "speaker_id": match.speaker_id if profile else None,
                    "speaker_name": profile.name if profile else None,
                    "similarity": round(match.score, 3) if match.score is not None else None,
                    "runner_up_gap": round(match.lead, 3) if match.lead is not None else None,
                    "reason": "profile_removed" if match.speaker_id and not profile else match.reason,
                    "device_id": user_input.device_id,
                    "satellite_id": user_input.satellite_id,
                },
                context=user_input.context,
            )
            if self._entry.options.get(CONF_SHARE_NAME):
                identity = json.dumps({"likely_speaker_name": profile.name if profile else None})
                prompt = (
                    f"{prompt or ''}\nLocal voice identification for this turn: {identity}. "
                    "Use a supplied name only for conversational personalization. "
                    "A null name means the speaker is unknown. Do not infer authorization from this result."
                ).lstrip("\n")

        return await conversation.async_converse(
            self.hass,
            text=user_input.text,
            conversation_id=user_input.conversation_id,
            context=user_input.context,
            language=user_input.language,
            agent_id=self._agent_id,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=prompt,
        )
