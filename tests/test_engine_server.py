"""The Voice Engine's HTTP endpoint: authentication, limits and error mapping (no models needed)."""
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

import server
from embedding import BadSample

TOKEN = "a" * 32


class FakeEngine:
    dimension = 4
    fail: Exception | None = None

    def from_pcm(self, body):
        if self.fail:
            raise self.fail
        return np.array([1.0, 0.0, 0.0, 0.0])

    embedding = from_pcm


@pytest.fixture
def engine_url(socket_enabled):
    engine = FakeEngine()
    httpd = server.make_server(engine, TOKEN, ("127.0.0.1", 0))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", engine
    httpd.shutdown()
    httpd.server_close()


def call(url, path, *, token=TOKEN, data=None, ctype="application/octet-stream"):
    headers = {"Content-Type": ctype}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_health_needs_the_token(engine_url):
    url, _ = engine_url
    assert call(url, "/v1/health") == (200, {"version": 1, "dimension": 4})
    assert call(url, "/v1/health", token=None)[0] == 401
    assert call(url, "/v1/health", token="b" * 32)[0] == 401


def test_unknown_paths_and_types_are_refused(engine_url):
    url, _ = engine_url
    assert call(url, "/v1/other")[0] == 404
    assert call(url, "/v1/other", data=b"x")[0] == 404
    assert call(url, "/v1/embedding", data=b"x", ctype="text/plain")[0] == 415
    assert call(url, "/v1/embedding", data=b"x" * (server.MAX_PCM_BYTES + 1))[0] == 413


def test_embedding_and_error_mapping(engine_url):
    url, engine = engine_url
    assert call(url, "/v1/embedding", data=b"\0" * 64) == (200, {"embedding": [1.0, 0.0, 0.0, 0.0]})
    engine.fail = BadSample("quiet")
    assert call(url, "/v1/embedding", data=b"\0" * 64) == (422, {"error": "insufficient_speech"})
    engine.fail = RuntimeError("boom")
    assert call(url, "/v1/embedding", data=b"\0" * 64) == (500, {"error": "inference_failed"})
