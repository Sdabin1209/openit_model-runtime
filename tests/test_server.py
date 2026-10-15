import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ReleaseBundle
from helpers import GENERATOR_INPUT, PARSER_INPUT, SCHEMA_DIR

import model_runtime as mr
from model_runtime import server


class StubReader:
    """HttpReleaseReader 대신. releases: id → manifest 수정 함수 또는 BoundaryError."""

    def __init__(self, tmp, plan=None):
        self.tmp, self.plan, self.calls = Path(tmp), plan or {}, 0

    def load_bundle(self, rid, *, dest):
        self.calls += 1
        step = self.plan.get(rid, lambda m: m)
        if isinstance(step, BoundaryError):
            raise step
        b = mr.FakeBackend()
        cfgs = mr.fake_serving_configs(b)
        ad = {}
        for r in cfgs:
            d = self.tmp / rid / r; d.mkdir(parents=True, exist_ok=True); ad[r] = d
        m = {"release_id": rid, "runtime_contract_version": 1, "model_runtime_version": mr.RUNTIME_VERSION,
             "adapters": {r: {"adapter_hash": "sha256:" + "0" * 64, "serving_config": c, "serving_config_hash": mr.serving_config_hash(c)}
                          for r, c in cfgs.items()}}
        return ReleaseBundle(step(m), {}, ad)


def start(tmp, plan=None, tokens=None, **kw):
    env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_PUT_WAIT_S": "5"}
    env.update(kw)
    if tokens:
        env["MODEL_RUNTIME_TOKENS"] = tokens
    rd = StubReader(tmp, plan)
    svc = server.build_service(env, reader_factory=lambda: rd)
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(100):
        if svc.ready: break
        time.sleep(0.02)
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", svc, rd


def call(url, path, body=None, token=None, method=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


class Server(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def srv(self, **kw):
        s = start(self._t.name, **kw); self.addCleanup(s[0].shutdown); return s

    def test_health_ready_info_before_any_release(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/healthz")[0], 200)
        self.assertEqual(call(url, "/readyz")[0], 200)
        _, info, _ = call(url, "/v1/info")
        self.assertEqual((info["supported_contract_versions"], info["loaded"], info["serving_config_hash"]), ([1], [], {}))

    def test_put_load_then_parse_generate(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "ABSENT")
        s, body, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, body["state"], body["error"]), (200, "LOADED", None))
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "LOADED")
        s, out, h = call(url, "/v1/releases/r1/parse", PARSER_INPUT)
        self.assertEqual((s, out, h["X-Openit-Outcome"], h["X-Openit-Attempts"]), (200, {"items": []}, "ok", "1"))
        self.assertNotIn("X-Model-Runtime-Outcome", h)
        s, out, _ = call(url, "/v1/releases/r1/generate", GENERATOR_INPUT)
        self.assertEqual((s, set(out)), (200, {"response_text", "is_fallback"}))
        info = call(url, "/v1/info")[1]
        self.assertEqual(info["loaded"], ["r1"]); self.assertEqual(set(info["serving_config_hash"]), {"LORA_A", "LORA_B"})

    def test_put_idempotent_does_not_reload(self):
        _, url, _, rd = self.srv()
        call(url, "/v1/releases/r1", method="PUT"); s, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, b["state"], rd.calls), (200, "LOADED", 1))

    def test_unloaded_release_is_404(self):
        _, url, _, _ = self.srv()
        s, b, _ = call(url, "/v1/releases/nope/parse", PARSER_INPUT)
        self.assertEqual((s, b["code"]), (404, "NOT_FOUND"))

    def test_slow_load_returns_202_then_loaded(self):
        def slow(m):
            time.sleep(0.6); return m
        _, url, _, _ = self.srv(plan={"r1": slow}, MODEL_RUNTIME_PUT_WAIT_S="0.1")
        s, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, b["state"]), (202, "LOADING"))
        time.sleep(0.9)
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "LOADED")

    def test_failed_load_keeps_serving_release(self):
        def bad_hash(m):
            m["adapters"]["LORA_A"]["serving_config_hash"] = "sha256:" + "1" * 64; return m
        plan = {"bad": bad_hash,
                "ver": lambda m: dict(m, model_runtime_version="9.9.0"),
                "contract": lambda m: dict(m, runtime_contract_version=2),
                "pre": BoundaryError("FAILED_PRECONDITION", "PASS 아님"),
                "integ": BoundaryError("INTEGRITY_ERROR", "해시"),
                "down": BoundaryError("UNAVAILABLE", "릴리스 서버 불가")}
        _, url, _, _ = self.srv(plan=plan)
        call(url, "/v1/releases/good", method="PUT")
        for rid, want in (("bad", 412), ("ver", 422), ("contract", 422), ("pre", 412), ("integ", 422), ("down", 503)):
            s, b, _ = call(url, f"/v1/releases/{rid}", method="PUT")
            self.assertEqual(s, want, rid)
            st = call(url, f"/v1/releases/{rid}")[1]
            self.assertEqual((st["state"], st["error"]["code"]), ("FAILED", b["code"]))
            self.assertEqual(call(url, "/v1/releases/good/parse", PARSER_INPUT)[0], 200)   # 서빙 중인 릴리스는 그대로
        self.assertEqual(call(url, "/v1/info")[1]["loaded"], ["good"])

    def test_invalid_input_is_400(self):
        _, url, _, _ = self.srv()
        call(url, "/v1/releases/r1", method="PUT")
        s, out, _ = call(url, "/v1/releases/r1/parse", {"utterance": 1})
        self.assertEqual((s, out["code"], out["retryable"]), (400, "INVALID_RECORD", False))

    def test_token_required_except_health(self):
        _, url, _, _ = self.srv(tokens="svc=secret")
        self.assertEqual(call(url, "/healthz")[0], 200); self.assertEqual(call(url, "/readyz")[0], 200)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 403)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT", token="secret")[0], 200)

    def test_rejects_when_queue_would_exceed_wait(self):
        _, url, svc, _ = self.srv(MODEL_RUNTIME_QUEUE_WAIT_S="1.0")
        call(url, "/v1/releases/r1", method="PUT")
        svc.avg_s = 2.0; svc.pending = 1   # 이미 처리 중인 호출이 있고 평균 2초
        s, b, _ = call(url, "/v1/releases/r1/parse", PARSER_INPUT)
        self.assertEqual((s, b["code"]), (503, "UNAVAILABLE"))
        svc.pending = 0
        self.assertEqual(call(url, "/v1/releases/r1/parse", PARSER_INPUT)[0], 200)

    def test_old_paths_gone(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/v1/parse", PARSER_INPUT)[0], 404)
