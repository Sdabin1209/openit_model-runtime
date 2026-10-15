import json
import threading
import unittest
import urllib.error
import urllib.request

from helpers import GENERATOR_INPUT, PARSER_INPUT, SCHEMA_DIR

import model_runtime as mr
from model_runtime import server


def start(tokens=None):
    env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_PORT": "0"}
    if tokens:
        env["MODEL_RUNTIME_TOKENS"] = tokens
    svc = server.build_service(env)
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def call(url, path, body=None, token=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


class Server(unittest.TestCase):
    def setUp(self):
        self.srv, self.url = start(); self.addCleanup(self.srv.shutdown)

    def test_health_ready_info(self):
        self.assertEqual(call(self.url, "/healthz")[0], 200)
        self.assertEqual(call(self.url, "/readyz")[0], 200)
        s, body, _ = call(self.url, "/v1/info")
        self.assertEqual(body["runtime_version"], mr.RUNTIME_VERSION)

    def test_parse_and_generate_conform(self):
        s, out, h = call(self.url, "/v1/parse", PARSER_INPUT)
        self.assertEqual((s, out, h["X-Model-Runtime-Outcome"]), (200, {"items": []}, "ok"))
        s, out, _ = call(self.url, "/v1/generate", GENERATOR_INPUT)
        self.assertEqual(s, 200)
        self.assertEqual(set(out), {"response_text", "is_fallback"})

    def test_invalid_input_is_400_boundary_error(self):
        s, out, _ = call(self.url, "/v1/parse", {"utterance": 1})
        self.assertEqual((s, out["code"], out["retryable"]), (400, "INVALID_RECORD", False))
        self.assertEqual(call(self.url, "/v1/nope", {})[0], 404)

    def test_token_required_when_configured(self):
        srv, url = start("svc=secret"); self.addCleanup(srv.shutdown)
        self.assertEqual(call(url, "/v1/parse", PARSER_INPUT)[0], 403)
        self.assertEqual(call(url, "/v1/parse", PARSER_INPUT, "secret")[0], 200)
        self.assertEqual(call(url, "/healthz")[0], 200)
