"""계약 5.0.0 반영(ADR 0038): ID 위임, 모델 출력 파서, 본문 상한이 계약 값과 같다."""
import unittest

from model_runtime.components.extract_role import _parse
from model_runtime.wiring.schema_gate import violations


class ReleaseIdDelegatedToContractId(unittest.TestCase):
    def test_table(self):
        for v, ok in [("rel-1", True), ("a.b:c", True), ("a" * 128, True), ("a" * 129, False), ("_x", False), ("../x", False), ("ok\n", False), ("", False), ("한", False)]:
            for name in ("PutRequest", "InferRequest"):
                value = {"release_id": v} if name == "PutRequest" else {"release_id": v, "input": {}}
                bad = [x for x in violations(name, value) if x[0] == "/release_id"]
                self.assertEqual(not bad, ok, (name, v[:10], bad))


class ModelOutputParsedByContractStrictJson(unittest.TestCase):
    def test_rejects_what_the_contract_rejects(self):
        for raw in ['NaN', '{"a":1e999}', '{"a":1,"a":2}', '{"a":9007199254740993}', '[' * 65 + ']' * 65, None]:
            self.assertEqual(_parse(raw), (None, ["json"]), str(raw)[:20])

    def test_accepts_plain_json(self):
        self.assertEqual(_parse('{"a": [1, 2.5, "가"]}'), ({"a": [1, 2.5, "가"]}, []))


class BodyLimitEqualsContractRouteBinding(unittest.TestCase):
    def test_max_body_is_the_openapi_binding(self):
        from model_runtime.components.link_receive import MAX_BODY
        self.assertEqual(MAX_BODY, 4194304)   # openapi model-serving 의 x-max-body-bytes. 서버 시작 때 verify_route 도 대조한다(test_runtime_rows_344)


class AreaSchemasRejectNonFiniteNumbers(unittest.TestCase):
    """ADR 0038(5.0.1 schema_validator): 영역 스키마 검증도 계약 규칙으로 NaN·무한대를 값 전체에서 먼저 거부한다. 표준 검증기는 number 칸에 NaN 을 통과시켰다."""
    def test_nan_and_infinity_are_rejected_and_finite_passes(self):
        import json
        from pathlib import Path
        ok = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "examples" / "MetricsEvent.valid.json").read_text(encoding="utf-8"))
        key = next(k for k, v in ok.items() if isinstance(v, float))   # 이 예시의 number 칸
        self.assertEqual(violations("MetricsEvent", ok), [])
        for bad in (float("nan"), float("inf"), float("-inf")):
            self.assertIn(("/" + key, "finite"), violations("MetricsEvent", {**ok, key: bad}), bad)
