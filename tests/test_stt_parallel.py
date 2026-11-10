"""Recognition starts when the speech ends, in parallel with the provider's transcription."""
import asyncio
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from homeassistant.components import stt

from custom_components.sentient_voice_recognition.recognition import Match
from custom_components.sentient_voice_recognition.stt import VoiceRecognitionSTT

META = stt.SpeechMetadata(language="en", format=stt.AudioFormats.WAV, codec=stt.AudioCodecs.PCM,
                          bit_rate=stt.AudioBitRates.BITRATE_16, sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                          channel=stt.AudioChannels.CHANNEL_MONO)


class Handoff:
    def __init__(self): self.offered = []
    def clear(self): pass
    def offer(self, text, match): self.offered.append((text, match))


def make(provider, recognize):
    runtime = SimpleNamespace(handoff=Handoff(), profiles={"a": 1}, async_recognize=recognize, enrolling=False)
    entry = SimpleNamespace(entry_id="e", options={"stt_provider": "stt.x"}, runtime_data=runtime)
    obj = VoiceRecognitionSTT(entry)
    obj.hass = SimpleNamespace()
    return obj, runtime, patch.object(VoiceRecognitionSTT, "_provider", new_callable=PropertyMock, return_value=provider)


class Provider:
    """A cloud provider that takes a while to answer after the audio ends."""
    def __init__(self, events): self.events = events

    async def async_process_audio_stream(self, metadata, stream):
        async for _ in stream:
            pass
        self.events.append("stream_ended")
        await asyncio.sleep(0.05)
        self.events.append("text_ready")
        return stt.SpeechResult("turn on the lamp", stt.SpeechResultState.SUCCESS)


async def audio():
    for _ in range(3):
        yield b"\x01\x00" * 800


async def test_recognition_overlaps_the_provider_and_is_handed_over():
    events = []

    async def recognize(pcm, threshold):
        events.append("recognising")
        await asyncio.sleep(0.02)
        events.append("recognised")
        return Match("a", 0.9, "matched")

    obj, runtime, patched = make(Provider(events), recognize)
    with patched:
        result = await obj.async_process_audio_stream(META, audio())
    assert result.text == "turn on the lamp"
    assert events.index("recognising") < events.index("text_ready")  # started before the words were ready
    assert runtime.handoff.offered[0][1].speaker_id == "a"


async def test_engine_fault_still_returns_the_words():
    async def recognize(pcm, threshold):
        raise RuntimeError("engine fell over")

    obj, runtime, patched = make(Provider([]), recognize)
    with patched:
        result = await obj.async_process_audio_stream(META, audio())
    assert result.text == "turn on the lamp"
    assert runtime.handoff.offered[0][1].reason == "engine_unavailable"
