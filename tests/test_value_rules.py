"""2.4.0 값 규칙(요청 `fields`가 있을 때 check_action_extraction 뒤에 apply_value_rules, ADR 0024).
기대값은 계약 tests/test_value_rules.py(학습 데이터셋 정답 모양)에서 왔다. (a) 적용 사유가 있으면 바뀐 출력을 다시 생성 없이 200,
(b) 바꿀 수 없는 위반은 다시 생성하고 끝내 남으면 503 OUTPUT_INVALID, (c) fields가 없으면 이전 동작."""
import json
import unittest
from tempfile import TemporaryDirectory

import model_runtime as mr
from boundary_contracts.action_rules import check_action_extraction
from helpers import SCHEMA_DIR, cfgs_and_hashes
from test_server import call, start

FIELDS = {"fields": [
    {"slot": "Q10.days", "data_type": "integer", "unit": "day/week", "minimum": 0, "maximum": 7},
    {"slot": "Q9_2.duration_minutes", "data_type": "integer", "unit": "minute", "minimum": 0},
    {"slot": "Q7.does_not_drink", "data_type": "boolean"},
    {"slot": "Q7.frequency", "data_type": "number", "exclusive_minimum": 0},
    {"slot": "Q7.unit", "data_type": "option", "options": ["week", "month", "year"]},
]}
FLOW = {"order": ["Q7", "Q7_1", "Q7_2", "Q8_1", "Q9_2", "Q10"],
        "skips": [{"slot": "Q7.does_not_drink", "op": "==", "value": True, "skip_item": "Q7_1"},
                  {"slot": "Q7.does_not_drink", "op": "==", "value": True, "skip_item": "Q7_2"}]}


def inp(q):
    return {"question_id": q, "current_question": "질문", "user_utterance": "발화원문", "state": {}}


def v(key, value, status="ANSWERED", save=True, precision="exact"):
    return {"key": key, "value": value, "status": status, "save": save, "precision": precision}


def nxt(values, next_item):
    return {"action": "NEXT", "values": values, "next_item": next_item}   # NEXT는 reason·targets를 두지 않는다(ActionExtraction 스키마)


OUT_OF_RANGE = nxt([v("Q10.days", 8)], "END")   # 데이터셋: "여덟 일" -> REASK OUT_OF_RANGE, 값은 버린다
OK = nxt([v("Q10.days", 3)], "END")
TYPE_MISMATCH = nxt([v("Q10.days", 2.5)], "END")   # integer 칸에 소수
BELOW_MIN = nxt([v("Q9_2.duration_minutes", -5)], "Q10")
WANT_REASK = {"action": "REASK", "next_item": "Q10", "reason": "OUT_OF_RANGE", "targets": ["Q10.days"],
              "values": [{"key": "Q10.days", "precision": "unspecified", "save": False, "status": "UNCLEAR", "value": None}]}


class ServerValueRulesHelpers:
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def up(self, responder):
        s = start(self._t.name); self.addCleanup(s[0].shutdown)
        _, url, svc, _ = s
        call(url, "/v1/releases/r1", method="PUT")
        self.calls = []
        def rec(req):
            self.calls.append(1); return responder(req)
        svc.releases["r1"].session._backend = mr.FakeBackend(rec)
        return url

    def post(self, url, q, fields=FIELDS):
        body = {"input": inp(q), "flow": FLOW, **({"fields": fields} if fields is not None else {})}
        return call(url, "/v1/releases/r1/extract", body, wrap=False)


class ServerValueRules(ServerValueRulesHelpers, unittest.TestCase):
    def test_a_applicable_reason_returns_the_changed_output_without_regenerating(self):
        url = self.up(lambda r: json.dumps(OUT_OF_RANGE))
        s, b, h = self.post(url, "Q10")
        print("(a):", s, h.get("X-Openit-Protections"), b)
        self.assertEqual(s, 200)
        self.assertEqual({k: b[k] for k in WANT_REASK}, WANT_REASK)
        self.assertEqual(len(self.calls), 1)   # 다시 생성하지 않는다
        self.assertEqual(h["X-Openit-Protections"], "OUT_OF_RANGE:1")   # 사유만 센다(값 없음)
        self.assertEqual(check_action_extraction(inp("Q10"), b, FLOW), [])   # 바뀐 출력도 규칙을 통과한다

    def test_a_r1_correction_keeps_flow_valid(self):
        o = nxt([v("Q7.does_not_drink", True), v("Q7.frequency", 3), v("Q7.unit", "week")], "Q8_1")
        url = self.up(lambda r: json.dumps(o))
        s, b, h = self.post(url, "Q7")
        self.assertEqual((s, h.get("X-Openit-Protections")), (200, "R1_CORRECTED:1"))
        self.assertEqual(check_action_extraction(inp("Q7"), b, FLOW), [])

    def test_b_unfixable_violation_regenerates_then_503(self):
        for bad, code in ((TYPE_MISMATCH, "VALUE_TYPE_MISMATCH"), (BELOW_MIN, "VALUE_BELOW_MINIMUM")):
            url = self.up(lambda r, bad=bad: json.dumps(bad))
            q = "Q9_2" if bad is BELOW_MIN else "Q10"
            s, b, _ = self.post(url, q)
            self.assertEqual((s, b["details"]["reason"], b["details"]["attempts"]), (503, "OUTPUT_INVALID", 3), code)
            self.assertTrue(any(x.startswith(f"rule:{code}:") for x in b["details"]["violations"]), b["details"])
            self.assertEqual(len(self.calls), 3)
            self.assertNotIn("발화원문", json.dumps(b, ensure_ascii=False))

    def test_b_retry_recovers(self):
        seq = iter([json.dumps(TYPE_MISMATCH), json.dumps(OK)])
        url = self.up(lambda r: next(seq))
        s, b, h = self.post(url, "Q10")
        self.assertEqual((s, h["X-Openit-Attempts"], b["values"][0]["value"]), (200, "2", 3))

    def test_c_without_fields_is_rejected_400(self):   # 3.0.0: fields는 필수다(값 규칙 보증을 건너뛰는 길이 없다). 모델을 부르지 않는다
        for out, q in ((OUT_OF_RANGE, "Q10"), (TYPE_MISMATCH, "Q10"), (BELOW_MIN, "Q9_2")):
            url = self.up(lambda r, out=out: json.dumps(out))
            s, b, h = self.post(url, q, fields=None)
            self.assertEqual((s, b["code"]), (400, "INVALID_RECORD"))
            self.assertEqual(self.calls, [])

    def test_fields_must_match_schema(self):
        url = self.up(lambda r: json.dumps(OK))
        s, b, _ = self.post(url, "Q10", fields={"fields": [{"slot": "Q10.days"}]})
        self.assertEqual((s, b["code"]), (400, "INVALID_RECORD"))


class LibraryValueRules(unittest.TestCase):
    def session(self, responder):
        b = mr.FakeBackend(responder)
        cfgs, hashes = cfgs_and_hashes(b)
        return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)

    def test_library_same_rules(self):
        s = self.session(lambda r: json.dumps(OUT_OF_RANGE))
        out = s.extract(inp("Q10"), FLOW, FIELDS)
        self.assertEqual({k: out[k] for k in WANT_REASK}, WANT_REASK)
        self.assertEqual(dict(s.last_call_info.protections), {"OUT_OF_RANGE": 1})
        with self.assertRaises(mr.ModelOutputInvalid):
            self.session(lambda r: json.dumps(TYPE_MISMATCH)).extract(inp("Q10"), FLOW, FIELDS)
        with self.assertRaises(TypeError):   # fields는 필수 인자(3.0.0)
            self.session(lambda r: json.dumps(OUT_OF_RANGE)).extract(inp("Q10"), FLOW)


class FakeBackendScene(ServerValueRulesHelpers, unittest.TestCase):
    """가짜 백엔드의 칸 규칙(slots)이 발화의 수를 그대로 저장하는 장면: 범위 밖 값을 내는 모델을 흉내 낸다."""
    def scene(self, utterance, fields=FIELDS):
        slots = {"Q10": [{"key": "Q10.days", "type": "integer"}]}
        url = self.up(mr.make_default_responder(FLOW["order"], None, slots, FLOW["skips"]))
        body = {"input": {**inp("Q10"), "user_utterance": utterance}, "flow": FLOW, **({"fields": fields} if fields else {})}
        return call(url, "/v1/releases/r1/extract", body, wrap=False)

    def test_out_of_range_utterance(self):
        s, b, h = self.scene("일주일에 8일 해요")
        self.assertEqual((s, b["action"], b["reason"], b["values"][0]["value"], h["X-Openit-Protections"]), (200, "REASK", "OUT_OF_RANGE", None, "OUT_OF_RANGE:1"))
        s, b, h = self.scene("일주일에 8일 해요", fields=None)
        self.assertEqual((s, b["code"]), (400, "INVALID_RECORD"))   # fields가 없으면 거부(3.0.0)
        s, b, h = self.scene("일주일에 3일 해요")
        self.assertEqual((s, b["action"], b["values"][0]["value"], "X-Openit-Protections" in h), (200, "NEXT", 3, False))
