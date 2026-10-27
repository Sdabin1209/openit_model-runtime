"""fake 백엔드가 입력 문항에 맞는 유효한 ActionExtraction을 낸다(0.6.2). survey-service가 쓰는 turn-engine의 validate_extraction으로 직접 확인한다.
turn-engine은 시험 전용이며 pyproject에 핀하지 않는다(설치 방법: docs/runbook.md). 없으면 이 시험의 해당 부분은 건너뛴다."""
import copy
import json
import unittest

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import A_INPUT, B_INPUT, SCHEMA_DIR, assert_valid, cfgs_and_hashes

import model_runtime as mr
from model_runtime import server

def _turn_engine():
    """turn-engine은 설치하지 않았으면 건너뛴다(pyproject는 버전을 핀하지 않는다. 어떤 태그를 쓸지는 docs/runbook.md)."""
    pytest.importorskip("turn_engine")
    from turn_engine.flow import DATASET_V2_FLOW
    from turn_engine.validation import validate_extraction
    return DATASET_V2_FLOW, validate_extraction

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

    def test_passes_turn_engine_validate_extraction_for_every_item(self):
        DATASET_V2_FLOW, validate_extraction = _turn_engine()
        s = session(list(DATASET_V2_FLOW.order))
        for qid in DATASET_V2_FLOW.order:
            inp = a_input(qid)
            self.assertEqual(validate_extraction(inp, s.extract(inp), DATASET_V2_FLOW), [], qid)

    def test_legacy_fixed_output_is_rejected_by_validate_extraction(self):   # 이 변경의 동기(deploy CR-D22 1항)를 시험으로 남긴다
        DATASET_V2_FLOW, validate_extraction = _turn_engine()
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
        base = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}
        with TemporaryDirectory() as t:
            p = Path(t) / "flow.json"
            p.write_text(json.dumps(ORDER), encoding="utf-8")
            server.build_service({**base, "MODEL_RUNTIME_FAKE_FLOW_FILE": str(p)}).close()
            server.build_service({**base, "MODEL_RUNTIME_FAKE_EXTRACTION": '{"action":"NEXT","next_item":"END","values":[]}'}).close()
            for bad in ({"MODEL_RUNTIME_FAKE_FLOW_FILE": str(Path(t) / "none.json")}, {"MODEL_RUNTIME_FAKE_EXTRACTION": "{"}):
                with self.assertRaises(SystemExit):
                    server.build_service({**base, **bad})


SLOTS = {"Q1.D03": [{"key": "Q1.D03.diagnosed", "type": "boolean"}, {"key": "Q1.D03.on_medication", "type": "boolean"}],
         "Q4": [{"key": "Q4.answer", "type": "boolean"}], "Q8_1": [{"key": "Q8_1.days", "type": "integer"}]}
SKIPS = [{"slot": "Q4.answer", "op": "==", "value": False, "skip_item": "Q4_1"},
         {"slot": "Q8_1.days", "op": "==", "value": 0, "skip_item": "Q8_2"}]


def v2_session():
    order = ["Q1.D01", "Q1.D02", "Q1.D03", "Q1.D04", "Q4", "Q4_1", "Q5", "Q8_1", "Q8_2", "Q9_1"]
    b = mr.FakeBackend(mr.make_default_responder(order, None, SLOTS, SKIPS))
    cfgs, hashes = cfgs_and_hashes(b)
    return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)


def _val(key, value):
    return {"key": key, "precision": "exact", "save": True, "status": "ANSWERED", "value": value}


# (문항, 발화, 기대 출력). 규칙: boolean 칸은 비어 있지 않은 발화가 '아니'를 포함하면 False, 아니면 True. integer 칸은 첫 숫자열,
# 숫자가 없고 '아니'/'없'이 있으면 0, 그 밖은 빈 values. 칸 정의가 없는 문항, 공백뿐인 발화는 기존대로 빈 values(빈 문자열 발화는 입력 계약이 거부한다).
RULES = [
    ("Q1.D03", "네 있어요", {"action": "NEXT", "next_item": "Q1.D04", "values": [_val("Q1.D03.diagnosed", True)]}),
    ("Q1.D03", "아니요", {"action": "NEXT", "next_item": "Q1.D04", "values": [_val("Q1.D03.diagnosed", False)]}),
    ("Q1.D03", "아니 없어요 괜찮아요", {"action": "NEXT", "next_item": "Q1.D04", "values": [_val("Q1.D03.diagnosed", False)]}),
    ("Q1.D03", "십 년 전에 진단받았어요", {"action": "NEXT", "next_item": "Q1.D04", "values": [_val("Q1.D03.diagnosed", True)]}),
    ("Q1.D03", "   ", {"action": "NEXT", "next_item": "Q1.D04", "values": []}),
    ("Q1.D01", "네", {"action": "NEXT", "next_item": "Q1.D02", "values": []}),          # 칸 정의 없음
    ("Q4", "네 피웠어요", {"action": "NEXT", "next_item": "Q4_1", "values": [_val("Q4.answer", True)]}),
    ("Q4", "아니요 안 피웠어요", {"action": "NEXT", "next_item": "Q5", "values": [_val("Q4.answer", False)]}),   # Q4_1 건너뜀
    ("Q8_1", "이틀 했어요 2일", {"action": "NEXT", "next_item": "Q8_2", "values": [_val("Q8_1.days", 2)]}),
    ("Q8_1", "3번이요", {"action": "NEXT", "next_item": "Q8_2", "values": [_val("Q8_1.days", 3)]}),
    ("Q8_1", "아니요 안 해요", {"action": "NEXT", "next_item": "Q9_1", "values": [_val("Q8_1.days", 0)]}),       # Q8_2 건너뜀
    ("Q8_1", "없어요", {"action": "NEXT", "next_item": "Q9_1", "values": [_val("Q8_1.days", 0)]}),
    ("Q8_1", "글쎄요 잘 모르겠어요", {"action": "NEXT", "next_item": "Q8_2", "values": []}),                          # 정하지 못함
]


class FakeValues(unittest.TestCase):
    def test_rule_table(self):
        s = v2_session()
        for qid, utt, want in RULES:
            out = s.extract({**a_input(qid), "user_utterance": utt})
            assert_valid("ActionExtraction", out)
            self.assertEqual(out, want, (qid, utt))
            self.assertEqual(list(out), sorted(out), "키 순서 사전순")
            for v in out["values"]:
                self.assertEqual(list(v), sorted(v))

    def test_no_slots_keeps_empty_values_for_any_utterance(self):
        s = session(ORDER)
        self.assertEqual(s.extract({**a_input("Q1.D01"), "user_utterance": "네"})["values"], [])

    def test_passes_turn_engine_validate_extraction(self):
        pytest.importorskip("turn_engine")
        from turn_engine.flow import DATASET_V2_FLOW
        from turn_engine.validation import validate_extraction
        order = list(DATASET_V2_FLOW.order)
        slots = {"Q1.D03": SLOTS["Q1.D03"], "Q4": SLOTS["Q4"], "Q5": [{"key": "Q5.answer", "type": "boolean"}],
                 "Q7": [{"key": "Q7.does_not_drink", "type": "boolean"}], "Q8_1": SLOTS["Q8_1"], "Q9_1": [{"key": "Q9_1.days", "type": "integer"}]}
        skips = [{"slot": r.slot, "op": "==", "value": r.value, "skip_item": r.skip_item} for r in DATASET_V2_FLOW.skips]
        b = mr.FakeBackend(mr.make_default_responder(order, None, slots, skips))
        cfgs, hashes = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)
        n_saved = 0
        for qid in order:
            for utt in ("네", "아니요", "3일", "없어요", "   ", "글쎄요"):
                inp = {**a_input(qid), "user_utterance": utt}
                out = s.extract(inp)
                self.assertEqual(validate_extraction(inp, out, DATASET_V2_FLOW), [], (qid, utt))
                n_saved += bool(out["values"])
        self.assertGreater(n_saved, 10)   # 저장 경로를 실제로 지났다

    def test_flow_spec_file_and_errors(self):
        with TemporaryDirectory() as t:
            ok = Path(t) / "ok.json"
            ok.write_text(json.dumps({"order": ORDER, "slots": SLOTS, "skips": SKIPS}), encoding="utf-8")
            self.assertEqual(mr.load_fake_flow(str(ok)), ORDER)
            self.assertEqual(mr.load_fake_flow_spec(str(ok)), (SLOTS, SKIPS))
            lst = Path(t) / "l.json"
            lst.write_text(json.dumps(ORDER), encoding="utf-8")
            self.assertEqual(mr.load_fake_flow_spec(str(lst)), ({}, []))
            for i, bad in enumerate(({"slots": []}, {"slots": {"Q1": []}}, {"slots": {"Q1": [{"key": "Q1.a", "type": "string"}]}},
                                     {"skips": [{"slot": "a", "value": 1}]}, {"skips": [{"slot": "a", "op": "!=", "value": 1, "skip_item": "Q1"}]})):
                p = Path(t) / f"bad{i}.json"
                p.write_text(json.dumps({"order": ORDER, **bad}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    mr.load_fake_flow_spec(str(p))

    def test_server_env_with_slots(self):
        base = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}
        with TemporaryDirectory() as t:
            p = Path(t) / "flow.json"
            p.write_text(json.dumps({"order": ORDER, "slots": SLOTS, "skips": SKIPS}), encoding="utf-8")
            server.build_service({**base, "MODEL_RUNTIME_FAKE_FLOW_FILE": str(p)}).close()

    def test_serving_config_hash_unchanged(self):   # 가짜 응답 규칙은 serving_config에 들어가지 않는다(0.7.1에서 해시가 바뀌지 않음)
        h = lambda r: mr.serving_config_hash(r)
        a = mr.FakeBackend(mr.make_default_responder(ORDER))
        b = mr.FakeBackend(mr.make_default_responder(ORDER, None, SLOTS, SKIPS))
        self.assertEqual({r: h(c) for r, c in cfgs_and_hashes(a)[0].items()}, {r: h(c) for r, c in cfgs_and_hashes(b)[0].items()})


CORRECTIONS = [{"contains": "아까 말", "key": "Q4.answer", "value": False, "at": "Q5"},
               {"contains": "사실은 하루", "key": "Q8_1.days", "value": 1}]
V2_ORDER = ["Q1.D01", "Q1.D02", "Q1.D03", "Q1.D04", "Q4", "Q4_1", "Q5", "Q8_1", "Q8_2", "Q9_1"]


def corr_session():
    b = mr.FakeBackend(mr.make_default_responder(V2_ORDER, None, SLOTS, SKIPS, CORRECTIONS))
    cfgs, hashes = cfgs_and_hashes(b)
    return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)


class FakeCorrect(unittest.TestCase):
    """0.7.2: 정정(CORRECT). 앞서 한 답을 고치는 발화에서 CORRECTION 값을 낸다. 스키마(v2.0.2)의 action 열거에 CORRECT가 있고
    NEXT와 같이 reason·targets·note를 갖지 않는다."""
    def test_correct_rule_table(self):
        s = corr_session()
        cv = lambda k, v: {"key": k, "precision": "exact", "save": True, "status": "CORRECTION", "value": v}
        for qid, utt, want in (
            ("Q5", "아까 말한 거 틀렸어요", {"action": "CORRECT", "next_item": "Q5", "values": [cv("Q4.answer", False)]}),   # Q4=False -> Q4_1 건너뜀
            ("Q8_2", "사실은 하루예요", {"action": "CORRECT", "next_item": "Q8_2", "values": [cv("Q8_1.days", 1)]}),
            ("Q9_1", "사실은 하루예요", {"action": "CORRECT", "next_item": "Q8_2", "values": [cv("Q8_1.days", 1)]}),
        ):
            out = s.extract({**a_input(qid), "user_utterance": utt})
            assert_valid("ActionExtraction", out)
            self.assertEqual(out, want, (qid, utt))
            self.assertEqual(list(out), sorted(out))
            self.assertEqual(list(out["values"][0]), sorted(out["values"][0]))

    def test_at_restricts_and_no_match_is_unchanged(self):
        s = corr_session()
        # at=Q5 이므로 다른 문항에서는 정정이 아니라 기존 규칙(NEXT)
        out = s.extract({**a_input("Q4"), "user_utterance": "아까 말한 대로 네"})
        self.assertEqual(out["action"], "NEXT")
        # corrections를 주지 않으면 기존 동작 그대로
        self.assertEqual(v2_session().extract({**a_input("Q5"), "user_utterance": "아까 말한 거 틀렸어요"})["action"], "NEXT")

    def test_passes_turn_engine_validate_extraction(self):
        pytest.importorskip("turn_engine")
        from turn_engine.flow import DATASET_V2_FLOW
        from turn_engine.validation import validate_extraction
        order = list(DATASET_V2_FLOW.order)
        skips = [{"slot": r.slot, "op": "==", "value": r.value, "skip_item": r.skip_item} for r in DATASET_V2_FLOW.skips]
        corr = [{"contains": "고칠게요", "key": "Q4.answer", "value": False}, {"contains": "고칠게요 일수", "key": "Q8_1.days", "value": 0},
                {"contains": "고칠게요 술", "key": "Q1.D03.diagnosed", "value": True, "at": "Q5"}]
        b = mr.FakeBackend(mr.make_default_responder(order, None, {}, skips, corr))
        cfgs, hashes = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)
        n = 0
        for qid in order:
            for utt in ("고칠게요", "고칠게요 일수", "고칠게요 술"):
                inp = {**a_input(qid), "user_utterance": utt}
                out = s.extract(inp)
                self.assertEqual(validate_extraction(inp, out, DATASET_V2_FLOW), [], (qid, utt))
                n += out["action"] == "CORRECT"
        self.assertGreater(n, 20)

    def test_file_format_and_errors(self):
        with TemporaryDirectory() as t:
            ok = Path(t) / "ok.json"
            ok.write_text(json.dumps({"order": V2_ORDER, "slots": SLOTS, "skips": SKIPS, "corrections": CORRECTIONS}), encoding="utf-8")
            self.assertEqual(mr.load_fake_corrections(str(ok)), CORRECTIONS)
            self.assertEqual(mr.load_fake_flow_spec(str(ok)), (SLOTS, SKIPS))   # 기존 읽기는 불변
            lst = Path(t) / "l.json"
            lst.write_text(json.dumps(ORDER), encoding="utf-8")
            self.assertEqual(mr.load_fake_corrections(str(lst)), [])
            for i, bad in enumerate(({"corrections": {}}, {"corrections": [{"key": "Q4.answer", "value": True}]},
                                     {"corrections": [{"contains": " ", "key": "Q4.answer", "value": True}]},
                                     {"corrections": [{"contains": "a", "key": "bad", "value": True}]},
                                     {"corrections": [{"contains": "a", "key": "Q4.answer", "value": None}]},
                                     {"corrections": [{"contains": "a", "key": "Q99.answer", "value": True}]})):
                p = Path(t) / f"bad{i}.json"
                p.write_text(json.dumps({"order": V2_ORDER, **bad}), encoding="utf-8")
                with self.assertRaises(ValueError, msg=i):
                    mr.load_fake_corrections(str(p))

    def test_server_env_with_corrections(self):
        base = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}
        with TemporaryDirectory() as t:
            p = Path(t) / "flow.json"
            p.write_text(json.dumps({"order": V2_ORDER, "slots": SLOTS, "skips": SKIPS, "corrections": CORRECTIONS}), encoding="utf-8")
            server.build_service({**base, "MODEL_RUNTIME_FAKE_FLOW_FILE": str(p)}).close()
            p.write_text(json.dumps({"order": V2_ORDER, "corrections": [{"contains": "a", "key": "Q99.x", "value": 1}]}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                server.build_service({**base, "MODEL_RUNTIME_FAKE_FLOW_FILE": str(p)})


if __name__ == "__main__":
    unittest.main()
