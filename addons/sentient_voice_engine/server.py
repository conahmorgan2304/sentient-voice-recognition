"""Private, authenticated inference endpoint on the Supervisor network."""

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import threading

from download_models import MODEL_DIR, SPEAKER_MODEL, VAD_MODEL
from embedding import BadSample, SAMPLE_RATE, VoiceEngine, read_sample

PORT = 10487
MAX_WAV_BYTES = 4 * 1024 * 1024
MAX_PCM_BYTES = SAMPLE_RATE * 2 * 12
_LOGGER = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    """Serve fixed endpoints and keep audio entirely in memory."""

    timeout = 5

    def __init__(self, *args, engine: VoiceEngine, token: str, lock: threading.Lock):
        self._engine = engine
        self._authorization = f"Bearer {token}".encode()
        self._lock = lock
        super().__init__(*args)

    def log_message(self, *args) -> None:
        """Do not log requests or credentials."""

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, allow_nan=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "").encode()
        if secrets.compare_digest(supplied, self._authorization):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path != "/v1/health":
            self._send(404, {"error": "not_found"})
            return
        self._send(200, {"version": 1, "dimension": self._engine.dimension})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self.path != "/v1/embedding":
            self._send(404, {"error": "not_found"})
            return
        content_type = self.headers.get_content_type()
        limits = {"audio/wav": MAX_WAV_BYTES, "application/octet-stream": MAX_PCM_BYTES}
        if content_type not in limits:
            self._send(415, {"error": "unsupported_audio"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= limits[content_type]:
            self._send(413, {"error": "invalid_size"})
            return
        if not self._lock.acquire(blocking=False):
            self._send(429, {"error": "busy"})
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise BadSample("Incomplete audio")
            if content_type == "audio/wav":
                vector = self._engine.embedding(*read_sample(body))
            else:
                vector = self._engine.from_pcm(body)
        except BadSample:
            self._send(422, {"error": "insufficient_speech"})
        except (TimeoutError, ConnectionError):
            self._send(408, {"error": "incomplete_audio"})
        except Exception:
            _LOGGER.exception("Speaker embedding inference failed")
            self._send(500, {"error": "inference_failed"})
        else:
            self._send(200, {"embedding": vector.tolist()})
        finally:
            self._lock.release()


def make_server(engine: VoiceEngine, token: str, address: tuple[str, int]) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        address, partial(Handler, engine=engine, token=token, lock=threading.Lock())
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    options = json.loads(Path("/data/options.json").read_text())
    token = options.get("token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token):
        raise SystemExit("Set an engine token of 24–128 ASCII letters, numbers, hyphens or underscores.")
    engine = VoiceEngine(MODEL_DIR / SPEAKER_MODEL, MODEL_DIR / VAD_MODEL)
    with make_server(engine, token, ("0.0.0.0", PORT)) as server:
        _LOGGER.info("Sentient Voice Engine is ready on its internal port")
        server.serve_forever()


if __name__ == "__main__":
    main()
