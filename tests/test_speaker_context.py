"""A speaker result is shown only to the agent handling that exact request."""
import asyncio
from types import SimpleNamespace

from custom_components.sentient_voice_recognition.speaker_context import (
    SpeakerTurn,
    speaker_for_turn,
    speaker_turn,
)


def req(text="hello", ctx=None):
    return SimpleNamespace(conversation_id="c", device_id="d", satellite_id="s", text=text,
                           context=ctx or object(), agent_id="conversation.sentient")


async def test_only_the_exact_request_in_scope_sees_the_result():
    request, result = req(), SpeakerTurn("a", "Alex")
    assert speaker_for_turn(request) is None
    with speaker_turn(request, "conversation.sentient", result):
        assert speaker_for_turn(request) is result
        assert speaker_for_turn(req("something else", request.context)) is None
        assert speaker_for_turn(req(ctx=object())) is None
        other = SimpleNamespace(**{**vars(request), "agent_id": "conversation.other"})
        assert speaker_for_turn(other) is None
    assert speaker_for_turn(request) is None  # gone once the turn ends


async def test_other_tasks_never_see_it():
    request = req()
    with speaker_turn(request, "conversation.sentient", SpeakerTurn("a", "Alex")):
        assert await asyncio.create_task(_look(request)) is None


async def _look(request):
    return speaker_for_turn(request)
