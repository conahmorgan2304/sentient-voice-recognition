"""Expose a speaker result only to the agent handling the current Assist turn."""

from asyncio import current_task
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from homeassistant.components.conversation import ConversationInput

from .const import DOMAIN


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    """A local result. Both fields are None when the speaker is unknown."""

    speaker_id: str | None
    display_name: str | None
    share_name: bool = False


def _request_key(request: ConversationInput) -> tuple:
    return (request.conversation_id, request.device_id, request.satellite_id, request.text)


_active_turn: ContextVar[tuple | None] = ContextVar("sentient_voice_recognition_turn", default=None)


@contextmanager
def speaker_turn(
    request: ConversationInput, agent_id: str, result: SpeakerTurn | None
) -> Iterator[None]:
    """Bind the result for one downstream call and restore the prior scope on exit."""
    value = None if result is None else (current_task(), request.context, _request_key(request), agent_id, result)
    token = _active_turn.set(value)
    try:
        yield
    finally:
        _active_turn.reset(token)


def speaker_for_turn(request: ConversationInput) -> SpeakerTurn | None:
    """Return the result only to the addressed agent for that exact request."""
    if (turn := _active_turn.get()) is None:
        return None
    task, context, request_key, agent_id, result = turn
    if task is current_task() and context is request.context and (
        request_key == _request_key(request) and agent_id == request.agent_id
    ):
        return result
    return None


SPEAKER_FOR_TURN = f"{DOMAIN}.speaker_for_turn"
