"""LoRA-A 출력 규칙 보증(ADR 0021, 계약 ADR 0010 결정 5). 기대값은 계약 리포 tests/test_action_rules.py의 손 사례(규칙 문장)에서 왔다.
서버 HTTP와 라이브러리 두 경로에서 같은 상황을 재현한다: 위반 출력 -> 재시도 후 503 OUTPUT_INVALID, 정상 출력 -> 200, flow 없는 옛 본문 -> 규칙 미검사 200."""
import json
import unittest
from tempfile import TemporaryDirectory

import model_runtime as mr
from helpers import SCHEMA_DIR, cfgs_and_hashes, question_flow_table
from test_server import call, start

FLOW = {"order": ["Q1", "Q4", "Q4_1", "Q5", "Q8_1", "Q8_2", "Q9"],
        "skips": [{"slot": "Q4.answer", "op": "==", "value": False, "skip_item": "Q4_1"},
                  {"slot": "Q8_1.days", "op": "==", "value": 0, "skip_item": "Q8_2"}]}
INPUT = {"question_id": "Q4", "current_question": "질문", "user_utterance": "답", "state": {}}


def out(next_item, answer=False):
    return {"action": "NEXT", "next_item": next_item,
            "values": [{"key": "Q4.answer", "value": answer, "precision": "exact", "status": "ANSWERED", "save": True}]}


GOOD = json.dumps(out("Q5"))          # Q4가 False면 Q4_1을 건너뛰고 Q5
SKIPPED_ASKED = json.dumps(out("Q4_1"))   # 건너뛰어야 할 Q4_1을 물음 -> SKIP_ORDER_MISMATCH


class ServerRules(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def up(self, responder, **kw):
        s = start(self._t.name, **kw); self.addCleanup(s[0].shutdown)
        _, url, svc, _ = s
        call(url, "/v1/releases/r1", method="PUT")
        svc.releases["r1"].session._backend = mr.FakeBackend(responder)
        return url

    def test_violating_output_is_503_output_invalid_after_retries(self):
        url = self.up(lambda req: SKIPPED_ASKED)
        s, b, _ = call(url, "/v1/releases/r1/extract", {"input": INPUT, "flow": FLOW})
        print("위반:", s, b)
        self.assertEqual((s, b["details"]["reason"], b["details"]["attempts"]), (503, "OUTPUT_INVALID", 3))
        self.assertIn("rule:SKIP_ORDER_MISMATCH:next_item", b["details"]["violations"])
        self.assertNotIn("답", json.dumps(b, ensure_ascii=False))   # 값·발화를 싣지 않는다

    def test_good_output_is_200(self):
        url = self.up(lambda req: GOOD)
        s, b, h = call(url, "/v1/releases/r1/extract", {"input": INPUT, "flow": FLOW})
        self.assertEqual((s, b["next_item"], h["X-Openit-Outcome"], h["X-Openit-Attempts"]), (200, "Q5", "ok", "1"))

    def test_retry_recovers_from_a_violation(self):
        seq = iter([SKIPPED_ASKED, GOOD])
        url = self.up(lambda req: next(seq))
        s, b, h = call(url, "/v1/releases/r1/extract", {"input": INPUT, "flow": FLOW})
        self.assertEqual((s, h["X-Openit-Attempts"]), (200, "2"))

    def test_old_body_without_flow_is_not_rule_checked(self):
        url = self.up(lambda req: SKIPPED_ASKED)
        s, b, _ = call(url, "/v1/releases/r1/extract", INPUT)
        self.assertEqual((s, b["next_item"]), (200, "Q4_1"))   # 2.x 호환: flow가 없으면 규칙 보증이 없다(HTTP.md I9)

    def test_body_matching_neither_schema_is_400(self):
        url = self.up(lambda req: GOOD)
        for bad in ({"input": INPUT}, {"input": INPUT, "flow": {"order": []}}, {"input": INPUT, "flow": FLOW, "x": 1}, {"foo": 1}, []):
            s, b, _ = call(url, "/v1/releases/r1/extract", bad)
            self.assertEqual((s, b["code"]), (400, "INVALID_RECORD"), bad)

    def test_input_limit_applies_to_input_not_flow(self):
        big_flow = {"order": FLOW["order"] + [f"Z{i}" for i in range(300)], "skips": FLOW["skips"]}
        url = self.up(lambda req: GOOD, MODEL_RUNTIME_MAX_INPUT_CHARS="600")
        self.assertEqual(call(url, "/v1/releases/r1/extract", {"input": INPUT, "flow": big_flow})[0], 200)   # flow는 모델에 들어가지 않는다
        long_in = {**INPUT, "user_utterance": "가" * 700}
        self.assertEqual(call(url, "/v1/releases/r1/extract", {"input": long_in, "flow": FLOW})[0], 400)

    def test_prompt_does_not_contain_flow(self):
        seen = []
        def rec(req):
            seen.append(req.prompt); return GOOD
        url = self.up(rec)
        call(url, "/v1/releases/r1/extract", {"input": INPUT, "flow": FLOW})
        self.assertNotIn("Q4_1", seen[0])   # 학습 글자 불변(D77): 프롬프트는 input만으로 만든다


class LibraryRules(unittest.TestCase):
    def session(self, responder):
        b = mr.FakeBackend(responder)
        cfgs, hashes = cfgs_and_hashes(b)
        return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)

    def test_flow_makes_violation_an_error(self):
        s = self.session(lambda req: SKIPPED_ASKED)
        with self.assertRaises(mr.ModelOutputInvalid) as c:
            s.extract(INPUT, FLOW)
        self.assertEqual(c.exception.attempts, 3)
        self.assertEqual(s.extract(INPUT)["next_item"], "Q4_1")   # flow=None이면 이전 동작

    def test_good_output_with_flow(self):
        self.assertEqual(self.session(lambda req: GOOD).extract(INPUT, FLOW)["next_item"], "Q5")


class RealFlowTable(unittest.TestCase):
    def test_survey_schema_flow_is_accepted_and_drives_the_check(self):
        flow = question_flow_table()
        first = flow["order"][0]
        i = {**INPUT, "question_id": first}
        ok = json.dumps({"action": "REASK", "next_item": first, "reason": "MISSING", "targets": [first + ".x"],
                         "values": [{"key": first + ".x", "value": None, "precision": "unspecified", "status": "MISSING", "save": False}]})
        s = LibraryRules().session(lambda req: ok)
        self.assertEqual(s.extract(i, flow)["next_item"], first)
        bad = json.dumps({**json.loads(ok), "next_item": flow["order"][-1]})
        with self.assertRaises(mr.ModelOutputInvalid):
            LibraryRules().session(lambda req: bad).extract(i, flow)


class ParityWithReferenceServer(unittest.TestCase):
    """계약 기준 서버(RuntimeReferenceServer)와 같은 입력에 같은 (상태, 사유)를 낸다."""
    CASES = [("규칙 위반 + flow", lambda: {"input": INPUT, "flow": FLOW}, SKIPPED_ASKED),
             ("정상 + flow", lambda: {"input": INPUT, "flow": FLOW}, GOOD),
             ("옛 본문, 위반 출력", lambda: INPUT, SKIPPED_ASKED),
             ("flow 없는 input만", lambda: {"input": INPUT}, GOOD),
             ("스키마 위반 출력 + flow", lambda: {"input": INPUT, "flow": FLOW}, '{"action":"NEXT"}')]

    def test_same_status_and_reason(self):
        import urllib.request
        try:
            from conformance.servers import RuntimeReferenceServer
        except ImportError:
            self.skipTest("conformance 패키지를 찾지 못했다")
        ref = RuntimeReferenceServer({"tok": "writer"}).start(); self.addCleanup(ref.stop)
        ref.available["r1"] = "ok"
        def ask(url, path, body, token=None, method="POST"):
            req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                         headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, None
            except urllib.error.HTTPError as e:
                d = json.loads(e.read()).get("details") or {}
                return e.code, d.get("reason")
        import urllib.error
        ask(ref.url, "/v1/releases/r1", None, "tok", "PUT")
        t = TemporaryDirectory(); self.addCleanup(t.cleanup)
        for name, body, output in self.CASES:
            ref.extract_fn = lambda req, o=output: json.loads(o)
            want = ask(ref.url, "/v1/releases/r1/extract", body(), "tok")
            s = start(t.name); self.addCleanup(s[0].shutdown)
            call(s[1], "/v1/releases/r1", method="PUT")
            s[2].releases["r1"].session._backend = mr.FakeBackend(lambda req, o=output: o)
            got, b, _ = call(s[1], "/v1/releases/r1/extract", body())
            self.assertEqual((got, (b.get("details") or {}).get("reason") if got != 200 else None), want, name)
