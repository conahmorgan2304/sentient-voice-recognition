"""Tap Assist audio while delegating speech-to-text to the original provider."""

from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VoiceRecognitionEntry
from .const import CONF_STT, CONF_THRESHOLD, DEFAULT_THRESHOLD, MAX_CAPTURE_BYTES, SAMPLE_RATE
from .recognition import Match


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VoiceRecognitionEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the speech-to-text adapter."""
    async_add_entities([VoiceRecognitionSTT(entry)])


class VoiceRecognitionSTT(stt.SpeechToTextEntity):
    """Forward the original provider's capabilities and audio stream."""

    _attr_name = "Sentient Voice Recognition STT"
    _attr_icon = "mdi:microphone-message"

    def __init__(self, entry: VoiceRecognitionEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stt"

    @property
    def _provider(self) -> stt.Provider | stt.SpeechToTextEntity | None:
        provider = stt.async_get_speech_to_text_engine(self.hass, self._entry.options[CONF_STT])
        return None if isinstance(provider, VoiceRecognitionSTT) else provider

    @property
    def supported_languages(self):
        return provider.supported_languages if (provider := self._provider) else []

    @property
    def supported_formats(self):
        return provider.supported_formats if (provider := self._provider) else []

    @property
    def supported_codecs(self):
        return provider.supported_codecs if (provider := self._provider) else []

    @property
    def supported_bit_rates(self):
        return provider.supported_bit_rates if (provider := self._provider) else []

    @property
    def supported_sample_rates(self):
        return provider.supported_sample_rates if (provider := self._provider) else []

    @property
    def supported_channels(self):
        return provider.supported_channels if (provider := self._provider) else []

    @property
    def audio_processing(self):
        if provider := self._provider:
            return provider.audio_processing
        return stt.DEFAULT_AUDIO_PROCESSING

    def check_metadata(self, metadata: stt.SpeechMetadata) -> bool:
        provider = self._provider
        return provider is not None and provider.check_metadata(metadata)

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        runtime = self._entry.runtime_data
        runtime.handoff.clear()
        provider = self._provider
        if provider is None:
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        supported = (
            metadata.codec == stt.AudioCodecs.PCM
            and metadata.bit_rate == 16
            and metadata.channel == 1
            and metadata.sample_rate == SAMPLE_RATE
        )
        capture = supported and bool(runtime.profiles)
        audio = bytearray()

        async def tapped_stream():
            async for chunk in stream:
                remaining = MAX_CAPTURE_BYTES - len(audio)
                if remaining > 0:
                    audio.extend(chunk[:remaining])
                yield chunk

        process = (
            provider.internal_async_process_audio_stream
            if isinstance(provider, stt.SpeechToTextEntity)
            else provider.async_process_audio_stream
        )
        result = await process(metadata, tapped_stream() if capture else stream)
        if result.result == stt.SpeechResultState.SUCCESS and result.text:
            if capture:
                threshold = self._entry.options.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
                match = await runtime.async_recognize(bytes(audio), threshold)
            else:
                reason = "no_profiles" if supported else "unsupported_audio"
                match = Match(None, None, reason)
            runtime.handoff.offer(result.text, match)
        return result
