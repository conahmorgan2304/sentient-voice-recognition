"""Enrol a voice by speaking to a real speaker, so the profile matches how that speaker hears you."""

from dataclasses import dataclass, field
from io import BytesIO
import re
import wave

from .const import SAMPLE_RATE
from .recognition import Vector

WINDOW_SECONDS = 600  # the enrolment is open this long after it is started in settings
MIN_TAKE_SECONDS = 3
MIN_TAKE_BYTES = SAMPLE_RATE * 2 * MIN_TAKE_SECONDS
SENTENCES = (
    "Voice sample one, the morning sun shines over the hills and the garden is quiet",
    "Voice sample two, a silver train rolls past the river and stops beside the old bridge",
    "Voice sample three, the rain stopped just before sunset and the sky turned a deep blue",
)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def sample_index(text: str) -> int | None:
    """Recognise a deliberate sample, allowing transcription of the number as a digit."""
    words = _words(text)
    if len(words) < 8 or words[:2] != ["voice", "sample"]:
        return None
    return {"one": 0, "1": 0, "two": 1, "2": 1, "three": 2, "3": 2}.get(words[2])


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
    satellite_id: str
    speaker_id: str | None
    expires: float
    takes: list[tuple[bytes, Vector]] = field(default_factory=list)  # fingerprint and validated embedding
