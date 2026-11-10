"""Enrolling a voice by speaking to a real speaker."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.components import conversation, stt

from custom_components.sentient_voice_recognition import runtime as rt_mod
from custom_components.sentient_voice_recognition.const import ENGINE_DIMENSION, SAMPLE_RATE
from custom_components.sentient_voice_recognition.conversation import VoiceRecognitionAgent
from custom_components.sentient_voice_recognition.recognition import BadSample, Match
from custom_components.sentient_voice_recognition.runtime import ProfileError, Runtime
from custom_components.sentient_voice_recognition.satellite import ENROLLING, MIN_TAKE_BYTES, SENTENCES
from custom_components.sentient_voice_recognition.stt import VoiceRecognitionSTT

from tests.test_stt_parallel import META

TAKE = b"\x01\x00" * (SAMPLE_RATE * 4)  # four seconds


def take(i):
    """Each real recording differs; four seconds of audio, made unique by its first sample."""
    return i.to_bytes(2, "little") + TAKE[2:]


def vec(i):
    v = [1.0] + [0.0] * (ENGINE_DIMENSION - 1)
    v[1 + i] = 0.3
    norm = sum(x * x for x in v) ** 0.5
    return tuple(x / norm for x in v)


@pytest.fixture
def runtime(hass):
    engine = SimpleNamespace(calls=0)

    async def embed(audio, *, wav):
        engine.calls += 1
        if audio.startswith(b"RIFF") and len(audio) < 1000:
            raise BadSample
        return vec(engine.calls)

    engine.async_embed = embed
    return Runtime(hass, "entry", engine)


async def test_three_takes_create_the_profile_and_the_speaker_guides_each_step(runtime):
    first = runtime.start_satellite_enrollment("Alex")
    assert first == SENTENCES[0] and runtime.enrolling
    say, more = await runtime.async_satellite_take(take(1))
    assert SENTENCES[1] in say and more
    say, more = await runtime.async_satellite_take(take(2))
    assert SENTENCES[2] in say and more
    say, more = await runtime.async_satellite_take(take(3))
    assert "saved Alex" in say and not more and not runtime.enrolling
    assert [p.name for p in runtime.profiles.values()] == ["Alex"]


async def test_a_short_or_unusable_take_is_asked_for_again_without_counting(runtime):
    runtime.start_satellite_enrollment("Alex")
    say, more = await runtime.async_satellite_take(b"\0" * (MIN_TAKE_BYTES - 2))
    assert "a little short" in say and SENTENCES[0] in say and more
    assert len(runtime.enrollment.takes) == 0


async def test_existing_name_replaces_that_persons_samples_and_bad_names_are_refused(runtime):
    runtime.start_satellite_enrollment("Alex")
    for i in range(3):
        await runtime.async_satellite_take(take(i + 1))
    (speaker_id,) = runtime.profiles
    runtime.start_satellite_enrollment("alex")
    assert runtime.enrollment.speaker_id == speaker_id
    with pytest.raises(ProfileError):
        runtime.start_satellite_enrollment("Alex 123")


async def test_it_closes_by_itself(runtime):
    runtime.start_satellite_enrollment("Alex")
    with patch.object(rt_mod, "monotonic", return_value=10**9):
        assert not runtime.enrolling
    say, more = await runtime.async_satellite_take(TAKE)
    assert "has ended" in say and not more


async def test_stt_takes_the_audio_and_never_sends_it_to_a_provider(runtime):
    runtime.start_satellite_enrollment("Alex")
    entry = SimpleNamespace(entry_id="e", options={"stt_provider": "stt.x"}, runtime_data=runtime)
    obj = VoiceRecognitionSTT(entry)
    provider = SimpleNamespace(async_process_audio_stream=AsyncMock())

    async def audio():
        yield TAKE

    with patch.object(VoiceRecognitionSTT, "_provider", new_callable=PropertyMock, return_value=provider):
        result = await obj.async_process_audio_stream(META, audio())
    provider.async_process_audio_stream.assert_not_called()
    turn = runtime.handoff.consume_turn(result.text)
    assert result.result == stt.SpeechResultState.SUCCESS
    assert turn.match.reason == ENROLLING and turn.audio == TAKE


async def test_conversation_answers_with_the_next_sentence_and_does_not_forward(runtime, hass):
    runtime.start_satellite_enrollment("Alex")
    entry = SimpleNamespace(entry_id="e", options={"conversation_agent": "conversation.x"}, runtime_data=runtime)
    agent = VoiceRecognitionAgent(entry)
    agent.hass = hass
    runtime.handoff.offer("voice enrolment sample", Match(None, None, ENROLLING), TAKE)
    request = conversation.ConversationInput(
        text="voice enrolment sample", context=SimpleNamespace(), conversation_id="c", device_id="d",
        satellite_id="s", language="en", agent_id="conversation.sentient_voice_recognition")
    with patch.object(VoiceRecognitionAgent, "_agent", new_callable=PropertyMock, return_value=object()), \
            patch("homeassistant.components.conversation.async_converse", new=AsyncMock()) as forward:
        result = await agent.async_process(request)
    forward.assert_not_called()
    assert SENTENCES[1] in result.response.speech["plain"]["speech"] and result.continue_conversation


async def test_distinctness_names_the_nearest_other_voice(runtime):
    runtime.start_satellite_enrollment("Alex")
    for i in range(3):
        await runtime.async_satellite_take(take(i + 1))
    out = runtime.distinctness()
    assert out == [{"name": "Alex", "nearest": None, "nearest_score": None}]
