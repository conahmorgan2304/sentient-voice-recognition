"""Local commands, carrying a match through a conversation, and learning from strong matches."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import intent

from custom_components.sentient_voice_recognition import runtime as rt_mod
from custom_components.sentient_voice_recognition.const import ENGINE_DIMENSION, EVENT_IDENTIFIED
from custom_components.sentient_voice_recognition.conversation import VoiceRecognitionAgent, skip_locally
from custom_components.sentient_voice_recognition.recognition import BadSample, Match, cosine, normalized
from custom_components.sentient_voice_recognition.runtime import Profile, Runtime

SAT = "assist_satellite.kitchen"


def vec(*parts):
    v = [0.0] * ENGINE_DIMENSION
    for index, value in enumerate(parts):
        v[index] = value
    return normalized(v)


ALEX = vec(1.0)


@pytest.fixture
def runtime(hass):
    engine = SimpleNamespace(next=ALEX)

    async def embed(audio, *, wav):
        if isinstance(engine.next, Exception):
            raise engine.next
        return engine.next

    engine.async_embed = embed
    rt = Runtime(hass, "entry", engine)
    rt.profiles = {"a1": Profile("Alex", ALEX)}
    return rt


def agent_for(hass, runtime, **options):
    entry = SimpleNamespace(entry_id="e", options={"conversation_agent": "conversation.x", **options}, runtime_data=runtime)
    agent = VoiceRecognitionAgent(entry)
    agent.hass = hass
    return agent


def ask(text, satellite=SAT):
    return conversation.ConversationInput(
        text=text, context=Context(), conversation_id="c", device_id="d",
        satellite_id=satellite, language="en", agent_id="conversation.sentient_voice_recognition")


async def run(agent, request, local=None):
    """Process one turn; `local` is what Home Assistant's own commands would answer (None = no match)."""
    reply = None
    if local is not None:
        reply = intent.IntentResponse(language="en")
        reply.async_set_speech(local)
    with patch.object(VoiceRecognitionAgent, "_agent", new_callable=PropertyMock, return_value=object()), \
            patch("homeassistant.components.conversation.async_converse", new=AsyncMock(return_value="from the agent")) as forward, \
            patch("homeassistant.components.conversation.async_handle_intents", new=AsyncMock(return_value=reply)) as intents, \
            patch("custom_components.sentient_voice_recognition.conversation.async_get_chat_log") as opened:
        log = opened.return_value.__enter__.return_value
        result = await agent.async_process(request)
    return result, forward, intents, log


# ----- local commands ---------------------------------------------------------------------------------------
async def test_a_local_command_is_answered_at_once_and_the_speaker_is_still_reported(hass, runtime):
    events = []
    hass.bus.async_listen(EVENT_IDENTIFIED, events.append)
    agent = agent_for(hass, runtime, local_commands=True)
    request = ask("turn on the kitchen light")
    runtime.handoff.offer(request.text, Match("a1", 0.8, "matched"))
    result, forward, intents, log = await run(agent, request, local="Turned on the light")
    await hass.async_block_till_done()
    forward.assert_not_called()
    assert result.response.speech["plain"]["speech"] == "Turned on the light"
    assert not result.continue_conversation and result.conversation_id
    assert events[0].data["speaker_name"] == "Alex" and events[0].data["reason"] == "matched"
    log.async_add_assistant_content_without_tools.assert_called_once()  # so streaming speakers say it


async def test_anything_home_assistant_cannot_do_itself_still_goes_to_the_agent(hass, runtime):
    agent = agent_for(hass, runtime, local_commands=True)
    request = ask("what should I cook tonight")
    result, forward, intents, _ = await run(agent, request, local=None)
    intents.assert_awaited_once()
    forward.assert_awaited_once()
    assert result == "from the agent"


async def test_local_commands_are_off_unless_chosen(hass, runtime):
    agent = agent_for(hass, runtime)
    _, forward, intents, _ = await run(agent, ask("turn on the kitchen light"), local="Turned on the light")
    intents.assert_not_called()
    forward.assert_awaited_once()


async def test_a_failing_local_command_step_never_blocks_the_agent(hass, runtime):
    agent = agent_for(hass, runtime, local_commands=True)
    with patch.object(VoiceRecognitionAgent, "_agent", new_callable=PropertyMock, return_value=object()), \
            patch("homeassistant.components.conversation.async_converse", new=AsyncMock(return_value="from the agent")) as forward, \
            patch("homeassistant.components.conversation.async_handle_intents", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await agent.async_process(ask("turn on the kitchen light")) == "from the agent"
    forward.assert_awaited_once()


def test_questions_and_conversational_replies_are_left_for_the_agent():
    def named(name):
        return SimpleNamespace(intent=SimpleNamespace(name=name))

    assert skip_locally(named("HassGetState")) and skip_locally(named("HassNevermind")) and skip_locally(named("HassMediaSearchAndPlay"))
    assert not skip_locally(named("HassTurnOn")) and not skip_locally(named("HassLightSet"))


# ----- carrying a match through a conversation --------------------------------------------------------------
async def test_a_short_follow_up_keeps_the_voice_matched_a_moment_ago_on_that_speaker(hass, runtime):
    events = []
    hass.bus.async_listen(EVENT_IDENTIFIED, events.append)
    agent = agent_for(hass, runtime)
    first, second = ask("what is the weather doing tomorrow afternoon"), ask("yes")
    runtime.handoff.offer(first.text, Match("a1", 0.8, "matched"))
    await run(agent, first)
    await hass.async_block_till_done()
    runtime.handoff.offer(second.text, Match(None, None, "insufficient_speech"))
    await run(agent, second)
    await hass.async_block_till_done()
    assert [e.data["reason"] for e in events] == ["matched", "carried_over"]
    assert events[1].data["speaker_name"] == "Alex" and events[1].data["similarity"] is None


async def test_nothing_is_carried_to_another_speaker_after_the_window_or_when_switched_off(hass, runtime):
    runtime.remember_speaker(SAT, "a1")
    assert runtime.recent_speaker(SAT) == "a1"
    assert runtime.recent_speaker("assist_satellite.lounge") is None and runtime.recent_speaker(None) is None
    with patch.object(rt_mod, "monotonic", return_value=rt_mod.monotonic() + rt_mod.CARRY_SECONDS + 1):
        assert runtime.recent_speaker(SAT) is None

    events = []
    hass.bus.async_listen(EVENT_IDENTIFIED, events.append)
    agent = agent_for(hass, runtime, carry_speaker=False)
    runtime.remember_speaker(SAT, "a1")
    request = ask("yes")
    runtime.handoff.offer(request.text, Match(None, None, "insufficient_speech"))
    await run(agent, request)
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "insufficient_speech" and events[0].data["speaker_name"] is None


async def test_a_voice_that_did_not_match_is_never_given_the_earlier_name(hass, runtime):
    events = []
    hass.bus.async_listen(EVENT_IDENTIFIED, events.append)
    agent = agent_for(hass, runtime)
    runtime.remember_speaker(SAT, "a1")
    request = ask("tell me a long story about something else entirely")
    runtime.handoff.offer(request.text, Match(None, 0.3, "below_threshold"))
    await run(agent, request)
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "below_threshold" and events[0].data["speaker_name"] is None
    assert runtime.recent_speaker(SAT) is None  # someone else is talking now


# ----- learning from strong matches -------------------------------------------------------------------------
async def test_a_strong_match_nudges_the_profile_towards_how_the_person_sounds_here(runtime):
    runtime.adaptive = True
    heard = vec(1.0, 0.5)  # 0.89 alike
    runtime.engine.next = heard
    with patch.object(runtime._store, "async_delay_save") as save:
        match = await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
    learned = runtime.profiles["a1"]
    assert match.reason == "matched"
    assert cosine(learned.vector, heard) > cosine(ALEX, heard)
    assert learned.anchor == ALEX and learned.name == "Alex"
    save.assert_called_once()


async def test_learning_is_off_by_default_and_ignores_weak_or_close_calls(runtime):
    runtime.engine.next = vec(1.0, 0.5)
    await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
    assert runtime.profiles["a1"].vector == ALEX  # off

    runtime.adaptive = True
    runtime.engine.next = vec(1.0, 1.0)  # 0.71: a match, but not a strong one
    with patch.object(runtime._store, "async_delay_save"):
        assert (await runtime.async_recognize(b"\x01\x00" * 100, 0.65)).reason == "matched"
        assert runtime.profiles["a1"].vector == ALEX

        runtime.profiles["b2"] = Profile("Sam", vec(1.0, -0.3))  # a second voice close behind
        runtime.engine.next = vec(1.0, 0.35)
        assert (await runtime.async_recognize(b"\x01\x00" * 100, 0.65)).reason == "matched"
        assert runtime.profiles["a1"].vector == ALEX


async def test_learning_is_paced_and_can_never_drift_far_from_the_recorded_voice(runtime):
    runtime.adaptive = True
    runtime.engine.next = vec(1.0, 0.5)
    with patch.object(runtime._store, "async_delay_save"):
        await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
        once = runtime.profiles["a1"].vector
        await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
        assert runtime.profiles["a1"].vector == once  # too soon after the last lesson

        now = rt_mod.monotonic()
        for step in range(1, 200):  # a long run of strong matches from one odd-sounding room
            with patch.object(rt_mod, "monotonic", return_value=now + step * (rt_mod.LEARN_EVERY + 1)):
                await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
    assert cosine(runtime.profiles["a1"].vector, ALEX) >= rt_mod.LEARN_ANCHOR - 1e-9


async def test_learned_voices_survive_a_restart_and_a_rename_keeps_the_recorded_voice(hass, runtime):
    runtime.adaptive = True
    runtime.engine.next = vec(1.0, 0.5)
    with patch.object(runtime._store, "async_delay_save") as save:
        await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
    data = save.call_args.args[0]()
    assert data["profiles"]["a1"]["anchor"] == list(ALEX)
    await runtime.async_rename("a1", "Alexander")
    assert runtime.profiles["a1"].anchor == ALEX

    fresh = Runtime(hass, "entry", runtime.engine)
    with patch.object(fresh._store, "async_load", new=AsyncMock(return_value=data)):
        await fresh.async_load()
    assert fresh.profiles["a1"].anchor == ALEX and fresh.profiles["a1"].vector == runtime.profiles["a1"].vector


async def test_an_engine_fault_while_learning_is_on_still_answers(runtime):
    runtime.adaptive = True
    runtime.engine.next = BadSample()
    assert (await runtime.async_recognize(b"\x01\x00" * 100, 0.65)).reason == "insufficient_speech"


async def test_a_lesson_not_yet_written_is_saved_when_the_integration_closes(runtime):
    runtime.adaptive = True
    runtime.engine.next = vec(1.0, 0.5)
    with patch.object(runtime._store, "async_delay_save"), patch.object(runtime._store, "async_save", new=AsyncMock()) as save:
        await runtime.async_recognize(b"\x01\x00" * 100, 0.65)
        await runtime.async_close()
        save.assert_awaited_once()
        assert save.await_args.args[0]["profiles"]["a1"]["anchor"] == list(ALEX)
        await runtime.async_close()
        save.assert_awaited_once()  # nothing new to write
