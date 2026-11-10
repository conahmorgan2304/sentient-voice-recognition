"""Tap Assist audio while delegating speech-to-text to the original provider."""

import asyncio
from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VoiceRecognitionEntry
from .const import CONF_STT, CONF_THRESHOLD, DEFAULT_THRESHOLD, MAX_CAPTURE_BYTES, SAMPLE_RATE
from .recognition import Match
from .satellite import sample_index


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
        enrollment = runtime.enrollment if supported and runtime.enrolling else None
        capture = supported and bool(runtime.profiles)
        audio = bytearray()
        threshold = self._entry.options.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        recognition: asyncio.Task[Match] | None = None

        async def tapped_stream():
            nonlocal recognition
            async for chunk in stream:
                remaining = MAX_CAPTURE_BYTES - len(audio)
                if remaining > 0:
                    audio.extend(chunk[:remaining])
                yield chunk
            # the speech has ended: recognise the voice while the provider is still working out the words
            if capture:
                recognition = asyncio.create_task(runtime.async_recognize(bytes(audio), threshold))

        process = (
            provider.internal_async_process_audio_stream
            if isinstance(provider, stt.SpeechToTextEntity)
            else provider.async_process_audio_stream
        )
        try:
            result = await process(metadata, tapped_stream() if capture or enrollment is not None else stream)
            if result.result == stt.SpeechResultState.SUCCESS and result.text:
                if capture:
                    match = await self._finished(recognition, bytes(audio), threshold)
                else:
                    reason = "no_profiles" if supported else "unsupported_audio"
                    match = Match(None, None, reason)
                if enrollment is not None and sample_index(result.text) is not None:
                    runtime.handoff.offer(result.text, match, bytes(audio), enrollment)
                else:
                    runtime.handoff.offer(result.text, match)
            return result
        finally:
            if recognition is not None and not recognition.done():
                recognition.cancel()

    async def _finished(self, recognition: asyncio.Task | None, audio: bytes, threshold: float) -> Match:
        """The result of the recognition started when the speech ended (started now if the provider never drained the stream)."""
        runtime = self._entry.runtime_data
        if recognition is None:
            return await runtime.async_recognize(audio, threshold)
        try:
            return await recognition
        except asyncio.CancelledError:
            raise
        except Exception:  # an engine fault must never stop an answer
            return Match(None, None, "engine_unavailable")
