"""Voice profile storage, inference scheduling, and per-turn handoff."""

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
import logging
from time import monotonic
from uuid import uuid4
from weakref import WeakKeyDictionary

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
import aiohttp

from .const import DOMAIN, MAX_PROFILES, MAX_WAV_BYTES
from .satellite import MIN_TAKE_BYTES, SENTENCES, WINDOW_SECONDS, SatelliteEnrollment, pcm_to_wav
from .recognition import (
    BadSample, EngineBusy, EngineClient, Match, Vector, cosine, identify, normalized,
)

_LOGGER = logging.getLogger(__name__)
HANDOFF_SECONDS = 5
MIN_ENROLLMENT_SIMILARITY = 0.38
DUPLICATE_PROFILE_SIMILARITY = 0.85
ENGINE_RETRY_SECONDS = 5


STEADY_TAKES = 0.5  # below this the three samples differ a lot


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    vector: Vector


@dataclass(frozen=True, slots=True)
class PendingTurn:
    text_id: int
    digest: bytes
    expires: float
    match: Match
    audio: bytes | None = None
    enrollment: SatelliteEnrollment | None = None


class TurnHandoff:
    """Pass one result within the Assist task that performed transcription."""

    def __init__(self) -> None:
        self._pending: WeakKeyDictionary[asyncio.Task, PendingTurn] = WeakKeyDictionary()

    def clear(self) -> None:
        if (task := asyncio.current_task()) is not None:
            self._pending.pop(task, None)

    def offer(
        self, text: str, match: Match, audio: bytes | None = None,
        enrollment: SatelliteEnrollment | None = None,
    ) -> None:
        if (task := asyncio.current_task()) is not None:
            self._pending[task] = PendingTurn(
                id(text), sha256(text.encode("utf-8", "surrogatepass")).digest(), monotonic() + HANDOFF_SECONDS, match, audio, enrollment
            )

    def consume_turn(self, text: str) -> PendingTurn | None:
        task = asyncio.current_task()
        if task is None or (turn := self._pending.pop(task, None)) is None:
            return None
        if (
            turn.expires < monotonic()
            or turn.text_id != id(text)
            or turn.digest != sha256(text.encode("utf-8", "surrogatepass")).digest()
        ):
            return None
        return turn

    def consume(self, text: str) -> Match | None:
        turn = self.consume_turn(text)
        return None if turn is None else turn.match


class ProfileError(ValueError):
    """An error that can be displayed in the enrollment form."""

    def __init__(self, code: str, sample_index: int | None = None) -> None:
        super().__init__(code)
        self.sample_index = sample_index


def profile_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Use an atomic, owner-only store for biometric templates."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}", private=True, atomic_writes=True)


class Runtime:
    """Own the integration's live state."""

    def __init__(self, hass: HomeAssistant, entry_id: str, engine: EngineClient) -> None:
        self.hass = hass
        self.engine = engine
        self.handoff = TurnHandoff()
        self.profiles: dict[str, Profile] = {}
        self._store = profile_store(hass, entry_id)
        self._edit_lock = asyncio.Lock()
        self._take_lock = asyncio.Lock()
        self._closed = False
        self._recognizing = False
        self._retry_at = 0.0
        self.enrollment: SatelliteEnrollment | None = None
        self._enrollment_timer: asyncio.TimerHandle | None = None

    async def async_load(self) -> None:
        """Load profiles created for the current speaker model."""
        data = await self._store.async_load()
        if data is None:
            return
        for speaker_id, item in data["profiles"].items():
            self.profiles[speaker_id] = Profile(item["name"], normalized(item["vector"]))

    async def _async_save(self, profiles: dict[str, Profile]) -> None:
        await self._store.async_save(
            {
                "profiles": {
                    key: {"name": profile.name, "vector": list(profile.vector)}
                    for key, profile in profiles.items()
                }
            }
        )
        self.profiles = profiles

    async def async_close(self) -> None:
        """Finish any profile write before a new runtime loads the store."""
        async with self._edit_lock:
            self._closed = True
            self.cancel_satellite_enrollment()

    def _check_open(self) -> None:
        if self._closed:
            raise ProfileError("integration_reloaded")

    def _read_uploads(self, file_ids: tuple[str, ...]) -> tuple[bytes, ...]:
        invalid_sample = "sample_unreadable" if len(file_ids) > 1 else "bad_test_sample"
        with ExitStack() as stack:
            paths = []
            missing_index = None
            for index, file_id in enumerate(file_ids):
                try:
                    paths.append(stack.enter_context(process_uploaded_file(self.hass, file_id)))
                except (ValueError, OSError):
                    if missing_index is None:
                        missing_index = index
            if missing_index is not None:
                raise ProfileError(invalid_sample, missing_index)

            files = []
            for index, path in enumerate(paths):
                try:
                    with path.open("rb") as stream:
                        audio = stream.read(MAX_WAV_BYTES + 1)
                except OSError as err:
                    raise ProfileError(invalid_sample, index) from err
                if not audio or len(audio) > MAX_WAV_BYTES:
                    raise ProfileError(invalid_sample, index)
                files.append(audio)
            return tuple(files)

    def _check_name(self, name: str, speaker_id: str | None) -> str:
        name = name.strip()
        if not 1 <= len(name) <= 40 or not all(c.isalpha() or c in " .'-" for c in name):
            raise ProfileError("invalid_name")
        if any(
            key != speaker_id and profile.name.casefold() == name.casefold()
            for key, profile in self.profiles.items()
        ):
            raise ProfileError("duplicate_name")
        return name

    def _get_profile(self, speaker_id: str) -> Profile:
        if (profile := self.profiles.get(speaker_id)) is None:
            raise ProfileError("profile_missing")
        return profile

    async def _async_embed_uploads(self, files: tuple[str, ...]) -> list[Vector]:
        invalid_sample = "bad_samples" if len(files) > 1 else "bad_test_sample"
        try:
            samples = await self.hass.async_add_executor_job(self._read_uploads, files)
        except ProfileError:
            raise
        except (ValueError, OSError) as err:
            raise ProfileError(invalid_sample) from err
        return await self._async_embed_samples(samples)

    async def _async_embed_samples(self, samples: tuple[bytes, ...]) -> list[Vector]:
        if self._recognizing:
            raise ProfileError("engine_busy")
        vectors = []
        try:
            for index, sample in enumerate(samples):
                if sample in samples[:index]:
                    raise ProfileError("sample_duplicate", index)
                vectors.append(await self.engine.async_embed(sample, wav=True))
        except EngineBusy as err:
            raise ProfileError("engine_busy") from err
        except BadSample as err:
            code = "sample_unusable" if len(samples) > 1 else "bad_test_sample"
            raise ProfileError(code, index) from err
        except ProfileError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError) as err:
            raise ProfileError("engine_unavailable") from err
        return vectors

    async def async_enroll(
        self,
        name: str,
        files: tuple[str, ...] = (),
        *,
        speaker_id: str | None = None,
        recordings: tuple[bytes, ...] | None = None,
        enrollment: SatelliteEnrollment | None = None,
    ) -> float:
        """Create a profile or replace its template while retaining its ID.

        Returns how alike the three samples were (the lowest pairwise score): higher means a steadier recording."""
        if enrollment is None and len(files if recordings is None else recordings) != 3:
            raise ProfileError("bad_samples")
        for index, file_id in enumerate(files):
            if file_id in files[:index]:
                raise ProfileError("sample_duplicate", index)
        async with self._edit_lock:
            self._check_open()
            if enrollment is not None and (not self.enrolling or self.enrollment is not enrollment):
                raise ProfileError("enrolment_ended")
            if speaker_id is not None:
                name = self._get_profile(speaker_id).name
            elif len(self.profiles) >= MAX_PROFILES:
                raise ProfileError("profile_limit")
            name = self._check_name(name, speaker_id)
            if enrollment is not None:
                vectors = [vector for _, vector in enrollment.takes]
                if len(vectors) != len(SENTENCES):
                    raise ProfileError("bad_samples")
            elif recordings is None:
                vectors = await self._async_embed_uploads(files)
            else:
                vectors = await self._async_embed_samples(recordings)
            consistency = 1.0
            for index, vector in enumerate(vectors):
                for other in vectors[:index]:
                    score = cosine(vector, other)
                    consistency = min(consistency, score)
                    if score > 0.99999:
                        raise ProfileError("sample_duplicate", index)
                    if score < MIN_ENROLLMENT_SIMILARITY:
                        raise ProfileError("samples_inconsistent")
            vector = normalized(sum(values) for values in zip(*vectors, strict=True))
            if any(
                key != speaker_id and cosine(vector, profile.vector) >= DUPLICATE_PROFILE_SIMILARITY
                for key, profile in self.profiles.items()
            ):
                raise ProfileError("duplicate_voice")
            key = speaker_id or uuid4().hex
            if enrollment is not None:
                if not self.enrolling or self.enrollment is not enrollment:
                    raise ProfileError("enrolment_ended")
                # Claim the commit before the atomic write. An older take never clears a newer session.
                self.cancel_satellite_enrollment()
            await self._async_save({**self.profiles, key: Profile(name, vector)})
            return round(consistency, 2)

    # ----- enrolling from a speaker --------------------------------------------------------------------------
    @property
    def enrolling(self) -> bool:
        """A speaker enrolment is open (it closes by itself after a few minutes)."""
        if self.enrollment is not None and self.enrollment.expires <= monotonic():
            self.cancel_satellite_enrollment()
        return self.enrollment is not None

    def start_satellite_enrollment(self, name: str, satellite_id: str) -> str:
        """Open an enrolment: the next three long sentences spoken to a Voice Recognition speaker become the samples.

        A name that already has a profile replaces that person's samples. Returns the first sentence to read."""
        self._check_open()
        state = self.hass.states.get(satellite_id)
        if not satellite_id.startswith("assist_satellite.") or state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise ProfileError("satellite_unavailable")
        name = name.strip()
        existing = next((key for key, p in self.profiles.items() if p.name.casefold() == name.casefold()), None)
        if existing is None:
            if len(self.profiles) >= MAX_PROFILES:
                raise ProfileError("profile_limit")
            name = self._check_name(name, None)
        else:
            name = self.profiles[existing].name
        self.cancel_satellite_enrollment()
        self.enrollment = enrollment = SatelliteEnrollment(name, satellite_id, existing, monotonic() + WINDOW_SECONDS)
        self._enrollment_timer = self.hass.loop.call_later(WINDOW_SECONDS, self._expire_enrollment, enrollment)
        return SENTENCES[0]

    def _expire_enrollment(self, enrollment: SatelliteEnrollment) -> None:
        if self.enrollment is enrollment:
            self.cancel_satellite_enrollment()

    def distinctness(self) -> list[dict]:
        """Each saved voice and how close the nearest other voice is (high means they could be confused)."""
        out = []
        for key, profile in self.profiles.items():
            others = [(cosine(profile.vector, o.vector), o.name) for k, o in self.profiles.items() if k != key]
            best = max(others, default=None)
            out.append({"name": profile.name, "nearest": best[1] if best else None,
                        "nearest_score": round(best[0], 2) if best else None})
        return out

    def cancel_satellite_enrollment(self) -> None:
        """Close an open speaker enrolment without saving anything."""
        if self._enrollment_timer is not None:
            self._enrollment_timer.cancel()
            self._enrollment_timer = None
        if self.enrollment is not None:
            self.enrollment.takes.clear()
            self.enrollment = None

    async def async_satellite_take(
        self, pcm: bytes, enrollment: SatelliteEnrollment, index: int,
    ) -> tuple[str, bool]:
        """Accept the expected sample from the conversation's selected satellite."""
        ended = "That voice enrolment has ended. Start it again from the Voice Recognition settings."
        async with self._take_lock:
            if not self.enrolling or self.enrollment is not enrollment:
                return ended, False
            expected = len(enrollment.takes)
            again = SENTENCES[expected]
            if index != expected:
                return f"Please read the next sentence: {again}", True
            if len(pcm) < MIN_TAKE_BYTES:
                return f"That was a little short. Please say it again: {again}", True
            fingerprint = sha256(pcm).digest()
            if any(fingerprint == old for old, _ in enrollment.takes):
                return f"That sounds like a repeated sample. Please record a new take: {again}", True
            try:
                (vector,) = await self._async_embed_samples((pcm_to_wav(pcm),))
            except ProfileError as err:
                if not self.enrolling or self.enrollment is not enrollment:
                    return ended, False
                if err.args[0] in ("engine_busy", "engine_unavailable"):
                    return "I couldn't reach the voice engine for a moment. Please say it again.", True
                return f"I couldn't hear enough clear speech. Please say it again: {again}", True
            if not self.enrolling or self.enrollment is not enrollment:
                return ended, False
            enrollment.takes.append((fingerprint, vector))
            if len(enrollment.takes) < len(SENTENCES):
                return f"Thanks. Now say: {SENTENCES[len(enrollment.takes)]}", True
            try:
                consistency = await self.async_enroll(
                    enrollment.name, speaker_id=enrollment.speaker_id, enrollment=enrollment,
                )
            except (ProfileError, OSError) as err:
                if self.enrollment is enrollment:
                    self.cancel_satellite_enrollment()
                if isinstance(err, ProfileError) and err.args[0] == "enrolment_ended":
                    return ended, False
                _LOGGER.debug("Speaker enrolment failed: %s", err)
                return "I couldn't save the voice profile. Please start again from the settings.", False
            finally:
                if self.enrollment is enrollment:
                    # Assist may cancel the pipeline while the final take is still being checked.
                    enrollment.takes.pop()
            note = ("" if consistency >= STEADY_TAKES else " The three recordings varied quite a bit, so if matching is "
                    "unreliable, record again somewhere quieter.")
            return f"All done. I've saved {enrollment.name}'s voice from this speaker.{note}", False

    async def async_rename(self, speaker_id: str, name: str) -> None:
        """Change a display name without changing the speaker ID or template."""
        async with self._edit_lock:
            self._check_open()
            profile = self._get_profile(speaker_id)
            name = self._check_name(name, speaker_id)
            if name != profile.name:
                await self._async_save({**self.profiles, speaker_id: Profile(name, profile.vector)})

    async def async_test(self, file_id: str, threshold: float) -> Match:
        """Score an uploaded WAV against the same profiles as a live Assist turn."""
        async with self._edit_lock:
            self._check_open()
            if not self.profiles:
                raise ProfileError("no_profiles")
            (vector,) = await self._async_embed_uploads((file_id,))
            return self._identify(vector, threshold)

    async def async_delete(self, speaker_id: str) -> None:
        """Remove a profile from both disk and subsequent matches."""
        async with self._edit_lock:
            self._check_open()
            self._get_profile(speaker_id)
            await self._async_save({
                key: value for key, value in self.profiles.items() if key != speaker_id
            })

    def _identify(self, vector: Vector, threshold: float) -> Match:
        return identify(vector, {key: profile.vector for key, profile in self.profiles.items()}, threshold)

    async def async_recognize(self, pcm: bytes, threshold: float) -> Match:
        """Keep voice replies flowing when the optional engine is unavailable."""
        if not self.profiles:
            return Match(None, None, "no_profiles")
        if self._closed or self._edit_lock.locked() or self._recognizing:
            return Match(None, None, "recognition_busy")
        if monotonic() < self._retry_at:
            return Match(None, None, "engine_unavailable")
        if not pcm:
            return Match(None, None, "insufficient_speech")

        self._recognizing = True
        try:
            vector = await self.engine.async_embed(pcm, wav=False)
            return self._identify(vector, threshold)
        except BadSample:
            return Match(None, None, "insufficient_speech")
        except EngineBusy:
            return Match(None, None, "recognition_busy")
        except (TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError) as err:
            _LOGGER.debug("Local voice engine unavailable: %s", type(err).__name__)
            self._retry_at = monotonic() + ENGINE_RETRY_SECONDS
            return Match(None, None, "engine_unavailable")
        finally:
            self._recognizing = False
