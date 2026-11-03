"""운영 소요 시간 지표 (계약 METRICS.md '운영 소요 시간 지표', U71; ADR 0033). 가짜 백엔드·가짜 시계로, 호출 뒤 GET /metrics에 세 이름이 나타나는지와
next_item 불일치 때만 카운터가 1 오르는지를 본다. 실제 소요 시간 값은 재지 않는다(시계는 가짜)."""
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import A_INPUT, B_INPUT, GOOD_EXTRACTION
from test_server import call, start

ENGINE, QUEUE, OVERRIDDEN = "openit_model_engine_duration_seconds", "openit_model_queue_wait_seconds", "openit_next_item_overridden_total"
WRONG_NEXT = json.dumps({**GOOD_EXTRACTION, "next_item": "END"})   # REASK의 next_item은 입력 question_id(Q1.D03)여야 해서 서버가 이 값을 덮어쓴다


def sample(text, name, **labels):
    """/metrics 텍스트에서 이름과 라벨이 모두 맞는 첫 줄의 값."""
    for line in text.splitlines():
        m = re.match(rf"^{re.escape(name)}(?:\{{(.*)\}})? ([0-9.e+-]+)$", line)
        if m:
            have = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1) or ""))
            if all(have.get(k) == v for k, v in labels.items()):
                return float(m.group(2))
    return None


class Metrics(unittest.TestCase):
    def _up(self, **kw):
        t = TemporaryDirectory(); self.addCleanup(t.cleanup)
        srv, url, svc, _ = start(t.name, **kw); self.addCleanup(srv.shutdown)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 200)
        return url

    def test_three_names_appear_after_calls_and_metrics_is_open_without_token(self):
        url = self._up()
        self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)
        self.assertEqual(call(url, "/v1/releases/r1/respond", B_INPUT)[0], 200)
        st, text, _ = call(url, "/metrics")
        self.assertEqual(st, 200)
        self.assertEqual(sample(text, ENGINE + "_count", op="extract", outcome="ok", attempts="1"), 1.0)
        self.assertEqual(sample(text, ENGINE + "_count", op="respond", outcome="ok", attempts="1"), 1.0)
        self.assertEqual(sample(text, QUEUE + "_count", op="extract"), 1.0)
        self.assertIn("# TYPE " + OVERRIDDEN.replace("_total", ""), text)   # 카운터는 선언돼 있다(아직 0이면 줄이 없을 수 있다)
        self.assertNotIn("고혈압", text)   # 발화·출력 원문은 지표에 없다

    def test_overridden_counter_rises_only_when_the_server_changed_next_item(self):
        url = self._up(MODEL_RUNTIME_FAKE_EXTRACTION=WRONG_NEXT)
        self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)
        text = call(url, "/metrics")[1]
        self.assertEqual(sample(text, OVERRIDDEN, op="extract"), 1.0)
        self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)
        self.assertEqual(sample(call(url, "/metrics")[1], OVERRIDDEN, op="extract"), 2.0)

    def test_no_override_when_model_value_equals_server_value(self):
        url = self._up(MODEL_RUNTIME_FAKE_EXTRACTION=json.dumps(GOOD_EXTRACTION))
        self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)
        self.assertIsNone(sample(call(url, "/metrics")[1], OVERRIDDEN, op="extract"))


class Declared(unittest.TestCase):
    def test_metrics_is_denied_at_the_gateway(self):
        decl = json.loads((Path(__file__).resolve().parents[1] / "deploy-exposure.json").read_text(encoding="utf-8"))
        denied = [p for r in decl["routes"] if r["auth"]["kind"] == "denied" for p in r["paths"]]
        self.assertIn("/metrics", denied)


if __name__ == "__main__":
    unittest.main()
