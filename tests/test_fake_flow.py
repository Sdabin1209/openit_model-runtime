"""fake 백엔드가 입력 문항에 맞는 유효한 ActionExtraction을 낸다(0.6.2). survey-service가 쓰는 turn-engine의 validate_extraction으로 직접 확인한다.
turn-engine(v0.3.0)은 시험 전용 의존(dev extras)이다. 없으면 이 시험의 해당 부분은 건너뛴다."""
import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import A_INPUT, B_INPUT, SCHEMA_DIR, assert_valid, cfgs_and_hashes

import model_runtime as mr
from model_runtime import server

try:
    from turn_engine.flow import DATASET_V2_FLOW
    from turn_engine.validation import validate_extraction
    HAVE_TE = True
except ImportError:
    HAVE_TE = False

ORDER = ["Q1.D01", "Q1.D02", "Q1.D03", "Q2.D01", "Q3"]


def session(order=None, extraction=None):
    b = mr.FakeBackend(mr.make_default_responder(order, extraction))
    cfgs, hashes = cfgs_and_hashes(b)
    return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)


def a_input(qid):
    return {**copy.deepcopy(A_INPUT), "question_id": qid, "state": {}}


class FakeFlow(unittest.TestCase):
    def test_next_is_following_item_and_end_after_last(self):
        self.assertEqual(mr.fake_extraction_for("Q1.D02", ORDER), {"action": "NEXT", "next_item": "Q1.D03", "values": []})
        self.assertEqual(mr.fake_extraction_for("Q3", ORDER)["next_item"], "END")
        self.assertEqual(mr.fake_extraction_for("Q9", ORDER)["next_item"], "END")   # 표에 없는 문항: 설정 불일치는 검증이 UNKNOWN_ITEM으로 알린다

    def test_session_returns_schema_valid_output_per_input(self):
        s = session(ORDER)
        for qid, want in zip(ORDER, ORDER[1:] + ["END"]):
            out = s.extract(a_input(qid))
            assert_valid("ActionExtraction", out)
            self.assertEqual((out["action"], out["next_item"], out["values"]), ("NEXT", want, []))
        self.assertEqual(s.respond(B_INPUT).text, mr.backends.FAKE_RESPONSE_TEXT)

    def test_without_config_output_is_legacy_fixed(self):   # 호환
        out = session().extract(a_input("Q1.D01"))
        self.assertEqual(out, mr.backends.FAKE_EXTRACTION)

    def test_fixed_extraction_wins_over_flow(self):
        fixed = {"action": "NEXT", "next_item": "Q3", "values": []}
        self.assertEqual(session(ORDER, fixed).extract(a_input("Q1.D01")), fixed)

    @unittest.skipUnless(HAVE_TE, "turn-engine 없음(.[dev])")
    def test_passes_turn_engine_validate_extraction_for_every_item(self):
        s = session(list(DATASET_V2_FLOW.order))
        for qid in DATASET_V2_FLOW.order:
            inp = a_input(qid)
            self.assertEqual(validate_extraction(inp, s.extract(inp), DATASET_V2_FLOW), [], qid)

    @unittest.skipUnless(HAVE_TE, "turn-engine 없음(.[dev])")
    def test_legacy_fixed_output_is_rejected_by_validate_extraction(self):   # 이 변경의 동기(deploy CR-D22 1항)를 시험으로 남긴다
        inp = a_input("Q1.D01")
        v = validate_extraction(inp, session().extract(inp), DATASET_V2_FLOW)
        self.assertEqual([x.code for x in v], ["SKIP_ORDER_MISMATCH"])

    def test_flow_file_formats_and_errors(self):
        with TemporaryDirectory() as t:
            for name, data, ok in (("a.json", ORDER, True), ("b.json", {"order": ORDER}, True), ("c.json", [], False),
                                   ("d.json", ["Q1", "Q1"], False), ("e.json", {"x": 1}, False), ("f.json", [1], False)):
                p = Path(t) / name
                p.write_text(json.dumps(data), encoding="utf-8")
                if ok:
                    self.assertEqual(mr.load_fake_flow(str(p)), ORDER)
                else:
                    with self.assertRaises(ValueError):
                        mr.load_fake_flow(str(p))

    def test_server_env(self):
        base = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}
        with TemporaryDirectory() as t:
            p = Path(t) / "flow.json"
            p.write_text(json.dumps(ORDER), encoding="utf-8")
            server.build_service({**base, "MODEL_RUNTIME_FAKE_FLOW_FILE": str(p)}).close()
            server.build_service({**base, "MODEL_RUNTIME_FAKE_EXTRACTION": '{"action":"NEXT","next_item":"END","values":[]}'}).close()
            for bad in ({"MODEL_RUNTIME_FAKE_FLOW_FILE": str(Path(t) / "none.json")}, {"MODEL_RUNTIME_FAKE_EXTRACTION": "{"}):
                with self.assertRaises(SystemExit):
                    server.build_service({**base, **bad})


if __name__ == "__main__":
    unittest.main()
