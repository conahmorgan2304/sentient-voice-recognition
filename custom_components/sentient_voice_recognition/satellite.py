"""Enrol a voice by speaking to a real speaker, so the profile matches how that speaker hears you."""

from dataclasses import dataclass, field
from io import BytesIO
import wave

from .const import SAMPLE_RATE

ENROLLING = "enrolling"
WINDOW_SECONDS = 600  # the enrolment is open this long after it is started in settings
MIN_TAKE_SECONDS = 3
MIN_TAKE_BYTES = SAMPLE_RATE * 2 * MIN_TAKE_SECONDS
SENTENCES = (
    "Could you turn on the living room lamp and tell me what the weather will be like tomorrow?",
    "I'd like to know how warm it is upstairs before I open the bedroom window for a while.",
    "Please check whether the back door is closed, then tell me if any lights are still on downstairs.",
)


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap 16 kHz mono 16-bit audio so the engine reads it as an ordinary recording."""
    out = BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(pcm)
    return out.getvalue()


@dataclass(slots=True)
class SatelliteEnrollment:
    """One person's three recordings, taken from the speaker they are talking to."""

    name: str
    speaker_id: str | None
    expires: float
    takes: list[bytes] = field(default_factory=list)
