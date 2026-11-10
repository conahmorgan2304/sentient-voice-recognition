"""Local voice activity detection and speaker matching."""

from io import BytesIO
import math
from pathlib import Path
import threading
import wave

import numpy as np
import sherpa_onnx

SAMPLE_RATE = 16_000

MIN_VOICED_SAMPLES = int(SAMPLE_RATE * 1.25)
MAX_WAV_SECONDS = 20


class BadSample(ValueError):
    """An enrollment sample cannot produce a usable voice embedding."""


def normalized(vector: np.ndarray) -> np.ndarray:
    """Return a finite unit vector."""
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-6:
        raise BadSample("Invalid embedding")
    return vector / norm


def read_sample(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Read up to twenty seconds of 16-bit mono or stereo PCM WAV."""
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as audio:
            rate = audio.getframerate()
            channels = audio.getnchannels()
            if (
                audio.getsampwidth() != 2
                or channels not in (1, 2)
                or not 8_000 <= rate <= 48_000
                or not rate <= audio.getnframes() <= rate * MAX_WAV_SECONDS
            ):
                raise BadSample("Unsupported WAV format or duration")
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != audio.getnframes() * channels * 2:
                raise BadSample("Incomplete WAV audio")
    except (OSError, EOFError, wave.Error) as err:
        raise BadSample("Could not read WAV audio") from err

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return samples / 32768.0, rate


class VoiceEngine:
    """Run speaker embedding and voice detection on the CPU."""

    def __init__(self, speaker_model: Path, vad_model: Path) -> None:
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(speaker_model), num_threads=1, provider="cpu", debug=False
            )
        )
        self._vad_config = sherpa_onnx.VadModelConfig()
        self._vad_config.silero_vad.model = str(vad_model)
        self._vad_config.silero_vad.threshold = 0.55
        self._vad_config.silero_vad.min_speech_duration = 0.20
        self._vad_config.silero_vad.min_silence_duration = 0.24
        self._vad_config.silero_vad.max_speech_duration = 20
        self._vad_config.sample_rate = SAMPLE_RATE
        self._vad_config.num_threads = 1
        self._vad_config.provider = "cpu"
        self._vad = sherpa_onnx.VoiceActivityDetector(
            self._vad_config, buffer_size_in_seconds=30
        )
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        return self._extractor.dim

    def from_pcm(self, pcm: bytes) -> np.ndarray:
        """Extract a voice embedding from the Assist PCM stream."""
        usable_length = len(pcm) - len(pcm) % 2
        samples = np.frombuffer(pcm[:usable_length], dtype="<i2").astype(np.float32)
        return self.embedding(samples / 32768.0, SAMPLE_RATE)

    def embedding(self, samples: np.ndarray, rate: int) -> np.ndarray:
        """Ignore silence and require sufficient voiced audio."""
        if rate != SAMPLE_RATE:
            positions = np.arange(len(samples) * SAMPLE_RATE // rate) * rate / SAMPLE_RATE
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)

        window = self._vad_config.silero_vad.window_size
        if len(samples) < MIN_VOICED_SAMPLES:
            raise BadSample("Too little speech")

        with self._lock:
            self._vad.reset()
            try:
                samples = np.pad(samples, (0, SAMPLE_RATE // 2))
                for offset in range(0, len(samples) - window + 1, window):
                    self._vad.accept_waveform(samples[offset : offset + window])
                self._vad.flush()

                segments = []
                while not self._vad.empty():
                    segments.append(np.array(self._vad.front.samples, dtype=np.float32))
                    self._vad.pop()
                if sum(map(len, segments)) < MIN_VOICED_SAMPLES:
                    raise BadSample("Too little speech")

                stream = self._extractor.create_stream()
                stream.accept_waveform(SAMPLE_RATE, np.concatenate(segments))
                stream.input_finished()
                if not self._extractor.is_ready(stream):
                    raise BadSample("Speaker model needs more speech")
                vector = np.array(self._extractor.compute(stream), dtype=np.float32)
                return normalized(vector)
            finally:
                self._vad.reset()
