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
        self.assertTrue(2.9 <= dt < 20, dt)   # 아래쪽 경계는 시계가 빨리 갈 수 없으니 결정적이다. 위쪽은 엔진이 묶인 gate.wait(30)보다 한참 앞서 돌려주는지(매달리지 않는지)만 본다
        print(f"extract ENGINE_TIMEOUT {dt:.2f}s")

    def test_respond_over_limit_gives_engine_timeout_within_2s_default(self):
        url, _ = self.up(self.hang)
        s, b, _, dt = self.timed(url, "/v1/releases/r1/respond", B_INPUT)
        self.assertEqual((s, b["details"]["reason"]), (503, "ENGINE_TIMEOUT"))
        self.assertTrue(1.9 <= dt < 20, dt)   # 위와 같다
        print(f"respond ENGINE_TIMEOUT {dt:.2f}s")

    def test_extract_retries_up_to_max_attempts_when_budget_allows(self):
        calls = []
        def fast_bad(req):
            calls.append(1); return "아님"
        url, _ = self.up(fast_bad)
        s, b, _, _ = self.timed(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((len(calls), s, b["details"]["attempts"]), (3, 503, 3))

class RetryBudgetWithInjectedClock(unittest.TestCase):
    """재시도가 총 상한 안에서 멈추는 규칙(계약 '시간 상한 사슬' 2.2.0, ADR 0020)을 시계를 주입해 결정적으로 본다.
    왜 벽시계가 아닌가: 실제 sleep으로 0.4초씩 재면 부하에서 한 번이 길어져 시도 횟수가 달라진다. 가짜 시계는 generate가 부른 만큼만 간다."""

    def run_loop(self, step, deadline, max_attempts=3):
        from model_runtime.components import retry_loop
        now = {"t": 0.0}
        calls = []

        def generate(req):
            calls.append(req.attempt); now["t"] += step; return "아님"
        spec = {"role": "LORA_A", "prompt": "p", "constraint_schema": None, "decoding": {"seed": 1}, "max_attempts": max_attempts, "input": {}, "deadline": deadline}
        r = retry_loop.run(spec, generate=generate, parse=lambda raw: (None, ["json"]), check=lambda v: [], clock=lambda: now["t"])
        return r, calls

    def test_stops_when_remaining_budget_is_less_than_last_attempt(self):
        r, calls = self.run_loop(step=0.4, deadline=1.0)   # 시도 1(0.4) 뒤 남은 0.6 >= 0.4 -> 시도 2(0.8) 뒤 남은 0.2 < 0.4 -> 중단
        self.assertEqual((calls, r["attempts"]), ([0, 1], 2))

    def test_single_attempt_when_budget_is_below_one_attempt(self):
        r, calls = self.run_loop(step=0.3, deadline=0.5)   # 시도 1(0.3) 뒤 남은 0.2 < 0.3 -> 재시도 없음
        self.assertEqual((calls, r["attempts"]), ([0], 1))

    def test_all_attempts_when_budget_allows(self):
        r, calls = self.run_loop(step=0.1, deadline=100.0)
        self.assertEqual((calls, r["attempts"]), ([0, 1, 2], 3))

    def test_no_deadline_means_max_attempts(self):
        r, calls = self.run_loop(step=5.0, deadline=None)
        self.assertEqual(r["attempts"], 3)


if __name__ == "__main__":
    unittest.main()
