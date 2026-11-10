"""Fetch pinned model artifacts during the local add-on build."""

from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

MODEL_ROOT = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SPEAKER_MODEL = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
VAD_MODEL = "silero_vad.onnx"
MODEL_DIR = Path(__file__).with_name("models")
MODELS = (
    (
        SPEAKER_MODEL,
        "speaker-recongition-models",
        "c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5",
        32 * 1024 * 1024,
    ),
    (
        VAD_MODEL,
        "asr-models",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        4 * 1024 * 1024,
    ),
)


def main() -> None:
    MODEL_DIR.mkdir(exist_ok=True)
    for filename, release, digest, max_bytes in MODELS:
        url = f"{MODEL_ROOT}/{release}/{filename}"
        with urlopen(url, timeout=60) as response:
            data = response.read(max_bytes + 1)
        if len(data) > max_bytes or sha256(data).hexdigest() != digest:
            raise ValueError(f"Invalid model download: {filename}")
        (MODEL_DIR / filename).write_bytes(data)


if __name__ == "__main__":
    main()
