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
from custom_components.sentient_voice_recognition.satellite import MIN_TAKE_BYTES, SENTENCES
from custom_components.sentient_voice_recognition.stt import VoiceRecognitionSTT

from tests.test_stt_parallel import META

SATELLITE = "assist_satellite.kitchen"
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
    hass.states.async_set(SATELLITE, "idle")
    engine = SimpleNamespace(calls=0)

    async def embed(audio, *, wav):
        engine.calls += 1
        if audio.startswith(b"RIFF") and len(audio) < 1000:
            raise BadSample
        return vec(engine.calls)

    engine.async_embed = embed
    return Runtime(hass, "entry", engine)


async def test_three_takes_create_the_profile_and_the_speaker_guides_each_step(runtime):
    first = runtime.start_satellite_enrollment("Alex", SATELLITE)
    assert first == SENTENCES[0] and runtime.enrolling
    say, more = await runtime.async_satellite_take(take(1), runtime.enrollment, 0)
    assert SENTENCES[1] in say and more
    say, more = await runtime.async_satellite_take(take(2), runtime.enrollment, 1)
    assert SENTENCES[2] in say and more
    say, more = await runtime.async_satellite_take(take(3), runtime.enrollment, 2)
    assert "saved Alex" in say and not more and not runtime.enrolling
    assert [p.name for p in runtime.profiles.values()] == ["Alex"]
    assert runtime.engine.calls == 3


async def test_a_short_or_unusable_take_is_asked_for_again_without_counting(runtime):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    say, more = await runtime.async_satellite_take(b"\0" * (MIN_TAKE_BYTES - 2), runtime.enrollment, 0)
    assert "a little short" in say and SENTENCES[0] in say and more
    assert len(runtime.enrollment.takes) == 0


async def test_existing_name_replaces_that_persons_samples_and_bad_names_are_refused(runtime):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    for i in range(3):
        await runtime.async_satellite_take(take(i + 1), runtime.enrollment, i)
    (speaker_id,) = runtime.profiles
    runtime.start_satellite_enrollment("alex", SATELLITE)
    assert runtime.enrollment.speaker_id == speaker_id
    with pytest.raises(ProfileError):
        runtime.start_satellite_enrollment("Alex 123", SATELLITE)


async def test_it_closes_by_itself(runtime):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    enrollment = runtime.enrollment
    with patch.object(rt_mod, "monotonic", return_value=10**9):
        assert not runtime.enrolling
    say, more = await runtime.async_satellite_take(TAKE, enrollment, 0)
    assert "has ended" in say and not more


@pytest.mark.parametrize("text", ["Turn on the lamp", SENTENCES[0]])
async def test_stt_keeps_the_real_transcript_and_only_offers_explicit_samples(runtime, text):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    entry = SimpleNamespace(entry_id="e", options={"stt_provider": "stt.x"}, runtime_data=runtime)
    obj = VoiceRecognitionSTT(entry)

    async def recognize(metadata, stream):
        assert b"".join([chunk async for chunk in stream]) == TAKE
        return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)

    async def audio():
        yield TAKE

    provider = SimpleNamespace(async_process_audio_stream=recognize)
    with patch.object(VoiceRecognitionSTT, "_provider", new_callable=PropertyMock, return_value=provider):
        result = await obj.async_process_audio_stream(META, audio())
    turn = runtime.handoff.consume_turn(result.text)
    assert result.text == text
    assert turn.audio == (TAKE if text == SENTENCES[0] else None)


@pytest.mark.parametrize("satellite", [SATELLITE, "assist_satellite.lounge", None])
async def test_only_the_selected_satellite_can_add_a_sample(runtime, hass, satellite):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    entry = SimpleNamespace(entry_id="e", options={"conversation_agent": "conversation.x"}, runtime_data=runtime)
    agent = VoiceRecognitionAgent(entry)
    agent.hass = hass
    runtime.handoff.offer(SENTENCES[0], Match(None, None, "no_profiles"), TAKE, runtime.enrollment)
    request = conversation.ConversationInput(
        text=SENTENCES[0], context=SimpleNamespace(), conversation_id="c", device_id="d",
        satellite_id=satellite, language="en", agent_id="conversation.sentient_voice_recognition")
    with patch.object(VoiceRecognitionAgent, "_agent", new_callable=PropertyMock, return_value=object()), \
            patch("homeassistant.components.conversation.async_converse", new=AsyncMock()) as forward:
        result = await agent.async_process(request)
    if satellite == SATELLITE:
        forward.assert_not_called()
        assert SENTENCES[1] in result.response.speech["plain"]["speech"] and result.continue_conversation
        assert len(runtime.enrollment.takes) == 1
    else:
        forward.assert_awaited_once()
        assert runtime.enrollment.takes == []


@pytest.mark.parametrize("pause_at", ["embedding", "commit"])
async def test_cancel_and_restart_during_final_sample_does_not_save_old_profile(runtime, pause_at):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    old = runtime.enrollment
    await runtime.async_satellite_take(take(1), old, 0)
    await runtime.async_satellite_take(take(2), old, 1)
    started, resume = asyncio.Event(), asyncio.Event()
    operation = "_async_embed_samples" if pause_at == "embedding" else "async_enroll"
    original = getattr(runtime, operation)

    async def pause(*args, **kwargs):
        started.set()
        await resume.wait()
        return await original(*args, **kwargs)

    with patch.object(runtime, operation, side_effect=pause):
        pending = asyncio.create_task(runtime.async_satellite_take(take(3), old, 2))
        await asyncio.wait_for(started.wait(), 2)
        runtime.cancel_satellite_enrollment()
        runtime.start_satellite_enrollment("Sam", SATELLITE)
        resume.set()
        assert "has ended" in (await pending)[0]
    assert runtime.enrollment.name == "Sam"
    assert runtime.profiles == {}


async def test_distinctness_names_the_nearest_other_voice(runtime):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    for i in range(3):
        await runtime.async_satellite_take(take(i + 1), runtime.enrollment, i)
    out = runtime.distinctness()
    assert out == [{"name": "Alex", "nearest": None, "nearest_score": None}]


async def test_expiry_clears_validated_samples_without_another_request(runtime):
    with patch.object(rt_mod, "WINDOW_SECONDS", 0.05):
        runtime.start_satellite_enrollment("Alex", SATELLITE)
    enrolment = runtime.enrollment
    await runtime.async_satellite_take(take(1), enrolment, 0)
    fingerprint, vector = enrolment.takes[0]
    assert len(fingerprint) == 32 and len(vector) == ENGINE_DIMENSION
    await asyncio.sleep(0.06)
    assert runtime.enrollment is None and enrolment.takes == []


async def test_duplicate_recording_can_be_retried_without_another_embedding(runtime):
    runtime.start_satellite_enrollment("Alex", SATELLITE)
    enrolment = runtime.enrollment
    await runtime.async_satellite_take(take(1), enrolment, 0)
    reply, more = await runtime.async_satellite_take(take(1), enrolment, 1)
    assert more and "repeated sample" in reply and SENTENCES[1] in reply
    assert len(enrolment.takes) == 1 and runtime.engine.calls == 1
