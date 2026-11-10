"""Local engine client and conservative speaker matching."""

from dataclasses import dataclass
import math
import re

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ENGINE_DIMENSION, ENGINE_INSTALL_REPOSITORY, ENGINE_URL, MIN_MARGIN,
    REPOSITORY_ENGINE_URL,
)

type Vector = tuple[float, ...]


class BadSample(ValueError):
    """A sample has too little usable speech."""


class EngineBusy(Exception):
    """The engine is already processing a sample."""


@dataclass(frozen=True, slots=True)
class Match:
    speaker_id: str | None
    score: float | None
    reason: str
    lead: float | None = None


def valid_token(token: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token) is not None


def normalized(values) -> Vector:
    """Validate the current engine's embedding format and normalize it."""
    vector = tuple(float(value) for value in values)
    if len(vector) != ENGINE_DIMENSION:
        raise ValueError("Unexpected speaker embedding dimension")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm < 1e-6:
        raise ValueError("Invalid speaker embedding")
    return tuple(value / norm for value in vector)


def cosine(left: Vector, right: Vector) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def identify(vector: Vector, profiles: dict[str, Vector], threshold: float) -> Match:
    """Require both absolute similarity and a lead over the runner-up."""
    if not profiles:
        return Match(None, None, "no_profiles")
    ranked = sorted(
        ((cosine(vector, reference), speaker) for speaker, reference in profiles.items()),
        reverse=True,
    )
    score, speaker = ranked[0]
    score = max(-1.0, min(1.0, score))
    lead = max(0.0, score - ranked[1][0]) if len(ranked) > 1 else None
    if score < threshold:
        return Match(None, score, "below_threshold", lead)
    if lead is not None and lead < MIN_MARGIN:
        return Match(None, score, "ambiguous", lead)
    return Match(speaker, score, "matched", lead)


def engine_url(installation: str | None) -> str:
    """Resolve the Supervisor address for the selected app installation."""
    return REPOSITORY_ENGINE_URL if installation == ENGINE_INSTALL_REPOSITORY else ENGINE_URL


class EngineClient:
    """Talk only to the local add-on, with bounded time and no redirects."""

    def __init__(self, hass: HomeAssistant, token: str, url: str = ENGINE_URL) -> None:
        self._session = async_get_clientsession(hass)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._url = url

    async def async_check(self) -> None:
        async with self._session.get(
            f"{self._url}/v1/health",
            headers=self._headers,
            timeout=aiohttp.ClientTimeout(total=3),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError("Voice engine rejected the connection")
            health = await response.json()
            if health != {"version": 1, "dimension": ENGINE_DIMENSION}:
                raise ValueError("Incompatible voice engine")

    async def async_embed(self, audio: bytes, *, wav: bool) -> Vector:
        headers = {
            **self._headers,
            "Content-Type": "audio/wav" if wav else "application/octet-stream",
        }
        async with self._session.post(
            f"{self._url}/v1/embedding",
            data=audio,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5 if wav else 0.7),
            allow_redirects=False,
        ) as response:
            if response.status == 422:
                raise BadSample
            if response.status == 429:
                raise EngineBusy
            if response.status != 200:
                raise ValueError("Voice engine returned an unexpected status")
            result = await response.json()
        return normalized(result["embedding"])
