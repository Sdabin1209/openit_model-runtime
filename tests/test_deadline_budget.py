"""CONVENTIONS '시간 상한 사슬'(2.2.0, D106): 호출별 상한 extract 3.0초, respond 2.0초. 재시도는 남은 예산이 직전 시도 시간 이상일 때만.
가짜 느린 백엔드로 재현한다. 기대값은 계약 표다(배분값은 측정하지 않았다, ADR 0020)."""
import json
import threading
import time
import unittest
from tempfile import TemporaryDirectory

import model_runtime as mr
from model_runtime.components import fake_backend
from helpers import A_INPUT, B_INPUT, SCHEMA_DIR
from model_runtime import server
from test_server import call, start


class Defaults(unittest.TestCase):
    def setUp(self):
        self._d = TemporaryDirectory(); self.addCleanup(self._d.cleanup)

    def build(self, **kw):
        return server.build_service({"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1",
                                     "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_BUNDLE_DIR": self._d.name, **kw}, reader_factory=lambda: None)

    def test_contract_table_defaults(self):
        self.assertEqual(self.build().deadlines, {"extract": 3.0, "respond": 2.0})

    def test_env_overrides_per_role(self):
        svc = self.build(MODEL_RUNTIME_EXTRACT_DEADLINE_S="1.5", MODEL_RUNTIME_RESPOND_DEADLINE_S="0.5")
        self.assertEqual(svc.deadlines, {"extract": 1.5, "respond": 0.5})

    def test_old_single_deadline_variable_is_refused(self):   # 조용히 무시하면 3.5초가 남아 있다고 착각한다
        with self.assertRaises(SystemExit):
            self.build(MODEL_RUNTIME_REQUEST_DEADLINE_S="3.5")


class Budget(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.gate = threading.Event(); self.addCleanup(self.gate.set)

    def up(self, responder, **kw):
        s = start(self._t.name, **kw); self.addCleanup(s[0].shutdown)
        _, url, svc, _ = s
        call(url, "/v1/releases/r1", method="PUT")
        svc.releases["r1"].session._backend = mr.FakeBackend(responder)
        return url, svc

    def hang(self, req):
        self.gate.wait(30)
        return json.dumps(fake_backend.FAKE_EXTRACTION) if req.role == "LORA_A" else "문장입니다."

    def timed(self, url, path, body):
        t = time.monotonic(); s, b, h = call(url, path, body); return s, b, h, time.monotonic() - t

    def test_extract_over_limit_gives_engine_timeout_within_3s_default(self):
        url, _ = self.up(self.hang)
        s, b, _, dt = self.timed(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((s, b["details"]["reason"]), (503, "ENGINE_TIMEOUT"))
        self.assertTrue(2.9 <= dt < 3.4, dt)
        print(f"extract ENGINE_TIMEOUT {dt:.2f}s")

    def test_respond_over_limit_gives_engine_timeout_within_2s_default(self):
        url, _ = self.up(self.hang)
        s, b, _, dt = self.timed(url, "/v1/releases/r1/respond", B_INPUT)
        self.assertEqual((s, b["details"]["reason"]), (503, "ENGINE_TIMEOUT"))
        self.assertTrue(1.9 <= dt < 2.4, dt)
        print(f"respond ENGINE_TIMEOUT {dt:.2f}s")

    def test_extract_retry_stops_when_remaining_budget_is_less_than_last_attempt(self):
        calls = []
        def slow_bad(req):
            calls.append(time.monotonic()); time.sleep(0.4); return "아님"
        url, _ = self.up(slow_bad, MODEL_RUNTIME_EXTRACT_DEADLINE_S="1.0")
        s, b, _, dt = self.timed(url, "/v1/releases/r1/extract", A_INPUT)
        # 시도 1(0.4) 뒤 남은 0.6 >= 0.4 → 시도 2(0.8) 뒤 남은 0.2 < 0.4 → 중단. 상한 안에 끝난다
        self.assertEqual(len(calls), 2)
        self.assertEqual((s, b["details"]["reason"], b["details"]["attempts"]), (503, "OUTPUT_INVALID", 2))
        self.assertLess(dt, 1.0)
        print(f"extract attempts={len(calls)} {dt:.2f}s")

    def test_extract_retries_up_to_max_attempts_when_budget_allows(self):
        calls = []
        def fast_bad(req):
            calls.append(1); return "아님"
        url, _ = self.up(fast_bad)
        s, b, _, _ = self.timed(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((len(calls), s, b["details"]["attempts"]), (3, 503, 3))

    def test_respond_retry_stops_by_budget_and_falls_back(self):
        calls = []
        def slow_empty(req):
            calls.append(1); time.sleep(0.3); return ""
        url, _ = self.up(slow_empty, MODEL_RUNTIME_RESPOND_DEADLINE_S="0.5")
        s, b, h, dt = self.timed(url, "/v1/releases/r1/respond", B_INPUT)
        # 시도 1(0.3) 뒤 남은 0.2 < 0.3 → 재시도 없음. 대체 문장
        self.assertEqual((len(calls), s, h["X-Openit-Outcome"], h["X-Openit-Attempts"]), (1, 200, "fallback", "1"))
        self.assertLess(dt, 0.5)
        print(f"respond attempts={len(calls)} {dt:.2f}s")


if __name__ == "__main__":
    unittest.main()
