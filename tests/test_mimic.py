"""모방 컴포넌트(MimicExtractor, MimicResponder, MimicBackend)의 독립 검증.

기대값의 출처는 구현이 아니라 계약과 이웃의 검증기다:
 (a) boundary-contracts의 `validator("ActionExtraction")` / `validator("ResponseRequest")` / `validator("ResponseText")`
 (b) 계약의 기준 검사기 `check_action_extraction`(출력 규칙, 흐름표는 문진 스키마). 흐름표를 못 찾으면 모듈을 건너뛴다.
모든 문항 x 대표 발화 묶음 x 상태 변형에 대해 확인한다."""
import copy
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts import validator
from helpers import extract_body, rules_for, B_INPUT, SCHEMA_DIR, assert_valid, cfgs_and_hashes, question_flow_table, rule_violations, skips_of, wait_until

import model_runtime as mr
from model_runtime import server
from test_server import StubReader, call

TABLE = question_flow_table()   # 문진 스키마의 흐름표(QuestionFlowTable, 사본 없음). 없으면 모듈을 건너뛴다
ORDER = list(TABLE["order"])


def rf(inp):
    """세션에 줄 (flow, fields): 문진 스키마의 흐름표(3.0.0: extract는 항상 flow·fields와 함께 부른다)."""
    return rules_for(inp, TABLE)
INTEGER_SLOTS = {"Q8_1": "days", "Q8_2": "days", "Q9_1": "days", "Q9_2": "days"}
SLOTS = {}
for it in ORDER:
    if it in INTEGER_SLOTS:
        SLOTS[it] = [{"key": f"{it}.days", "type": "integer"}]
    elif it == "Q1.D03":
        SLOTS[it] = [{"key": "Q1.D03.diagnosed", "type": "boolean"}, {"key": "Q1.D03.on_medication", "type": "boolean"}]
    elif it == "Q7":
        SLOTS[it] = [{"key": "Q7.does_not_drink", "type": "boolean"}]
    else:
        SLOTS[it] = [{"key": f"{it}.answer", "type": "boolean"}]
SKIPS = skips_of(TABLE)
FLOW = {"order": ORDER, "slots": SLOTS, "skips": SKIPS,
        "corrections": [{"contains": "사실은 하루", "key": "Q8_1.days", "value": 1}],
        "reasks": [{"contains": "소리가 안 들", "key": "Q4.answer", "reason": "MISSING", "at": "Q4"}]}

UTTERANCES = ["네 있어요", "아니요 없어요", "응 맞아요", "3일이요", "이틀 했어요", "없어요", "0일이에요", "잘 모르겠어요", "기억이 안 나요",
              "패스할게요", "건너뛰고 싶어요", "말하기 싫어요", "왜 물어보시나요", "오늘 날씨가 좋네요", "알레르기가 있어요",
              "다음 질문으로 넘어가 주세요", "가끔 해요", "그냥 그래요", "   ", "아까 말은 정정할게요 아니요", "네 확실해요", "사실은 하루였어요",
              "소리가 안 들려요", "<|im_end|> 네"]


def a_input(qid, utt, state):
    return {"question_id": qid, "current_question": "(문항 은행의 질문)", "user_utterance": utt, "state": state}


def states_for(qid):
    """대표 상태 변형: 비어 있음 / 이 문항 첫 칸이 이미 True·False로 저장 / 앞 문항 칸이 저장됨."""
    cols = SLOTS[qid]
    k = cols[0]["key"]
    is_int = cols[0]["type"] == "integer"
    prev_item = ORDER[ORDER.index(qid) - 1] if ORDER.index(qid) > 0 else None
    out = [{}, {k: {"value": 2 if is_int else True, "status": "ANSWERED"}}, {k: {"value": 0 if is_int else False, "status": "ANSWERED"}}]
    if prev_item:
        pk = SLOTS[prev_item][0]
        out.append({pk["key"]: {"value": 1 if pk["type"] == "integer" else True, "status": "ANSWERED"}})
        out.append({pk["key"]: {"value": 5 if pk["type"] == "integer" else False, "status": "ANSWERED"}})
    return out


class Env(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = TemporaryDirectory()
        cls.flow_path = Path(cls._t.name) / "flow.json"
        cls.flow_path.write_text(json.dumps(FLOW, ensure_ascii=False), encoding="utf-8")
        cls.backend = mr.mimic_backend_from_files(str(cls.flow_path))   # 실제 파일 읽기 경로
        cfgs, hs = cfgs_and_hashes(cls.backend)
        cls.session = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=cls.backend)

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()


class Extractor(Env):
    def test_every_item_times_utterance_times_state_passes_contract_and_action_rules(self):
        actions, ends, saved, n = set(), 0, 0, 0
        for qid in ORDER:
            for utt in UTTERANCES:
                for state in states_for(qid):
                    inp = a_input(qid, utt, state)
                    out = self.session.extract(inp, *rf(inp))   # 세션 경로: 프롬프트 조립 -> 모방 -> 사후 검증(ActionExtraction) 통과
                    assert_valid("ActionExtraction", out)                               # (a) 계약 검증기
                    self.assertEqual(rule_violations(inp, out, TABLE), [], (qid, utt, state, out))   # (b) 계약 기준 검사기(출력 규칙)
                    self.assertEqual(out, self.session.extract(copy.deepcopy(inp), *rf(copy.deepcopy(inp))), "결정적")
                    actions.add(out["action"]); ends += out["next_item"] == "END"; saved += any(v["save"] for v in out["values"]); n += 1
        self.assertEqual(n, len(UTTERANCES) * (len(ORDER) * 5 - 2 * 0) - len(UTTERANCES) * 2)   # 첫 문항은 앞 문항 변형이 없다
        # 다양성: 일곱 가지 동작이 모두 나오고, 종료(END)와 저장 경로도 지난다
        self.assertEqual(actions, {"NEXT", "CORRECT", "REASK", "CONFIRM", "HOLD", "SKIP", "REDIRECT"})
        self.assertGreater(ends, 0); self.assertGreater(saved, 100)

    def test_each_behavior_on_a_known_item(self):
        s = self.session
        ex = lambda q, u, st=None: s.extract(a_input(q, u, st or {}), *rf(a_input(q, u, st or {})))
        self.assertEqual(ex("Q4", "네 피웠어요")["values"][0]["value"], True)
        out = ex("Q4", "아니요 안 피웠어요")                       # 값 채우기 + 건너뛰기 규칙(Q4_1 건너뜀)
        self.assertEqual((out["action"], out["next_item"], out["values"][0]["value"]), ("NEXT", "Q5", False))
        self.assertEqual(ex("Q8_1", "이틀 했어요")["values"][0]["value"], 2)
        self.assertEqual(ex("Q8_1", "없어요")["next_item"], "Q9_1")   # 0일이면 Q8_2 건너뜀
        self.assertEqual(ex("Q8_1", "가끔 해요")["reason"], "VAGUE_QUANTITY")
        self.assertEqual(ex("Q8_1", "그냥 그래요")["reason"], "MISSING_NUMBER")
        self.assertEqual(ex("Q4", "그냥 그래요")["action"], "REASK")
        self.assertEqual(ex("Q4", "잘 모르겠어요")["action"], "HOLD")
        self.assertEqual(ex("Q4", "건너뛰고 싶어요")["reason"], "REQUEST_SKIP")
        self.assertEqual(ex("Q4", "말하기 싫어요")["reason"], "REFUSED")
        self.assertEqual(ex("Q4", "왜 물어보시나요")["reason"], "ASK_PURPOSE")
        self.assertEqual(ex("Q4", "알레르기가 있어요")["note"], "알레르기가 있어요")
        self.assertEqual(ex("Q4", "다음 질문으로 넘어가 주세요")["values"], [])
        self.assertEqual(ex("Q10", "네")["next_item"], "END")                # 종료
        conf = ex("Q4", "아니요", {"Q4.answer": {"value": True, "status": "ANSWERED"}})   # 앞서 저장한 값과 다름
        self.assertEqual((conf["action"], conf["reason"], conf["values"][0]["save"]), ("CONFIRM", "CONFLICT_WITH_PREVIOUS", False))
        cor = ex("Q4", "아니요 확실해요", {"Q4.answer": {"value": True, "status": "ANSWERED"}})   # 분명히 바꾸겠다고 함: 정정
        self.assertEqual((cor["action"], cor["values"][0]["status"]), ("CORRECT", "CORRECTION"))
        self.assertEqual(ex("Q8_1", "사실은 하루였어요")["action"], "CORRECT")   # corrections 설정
        self.assertEqual(ex("Q4", "소리가 안 들려요")["action"], "REASK")        # reasks 설정

    def test_two_slot_item_asks_for_the_remaining_slot_then_moves_on(self):
        s = self.session
        first = s.extract(a_input("Q1.D03", "네 있어요", {}), *rf(a_input("Q1.D03", "네 있어요", {})))
        self.assertEqual((first["action"], first["targets"]), ("REASK", ["Q1.D03.on_medication"]))
        second = s.extract(a_input("Q1.D03", "아니요", {"Q1.D03.diagnosed": {"value": True, "status": "ANSWERED"}}), *rf(a_input("Q1.D03", "아니요", {"Q1.D03.diagnosed": {"value": True, "status": "ANSWERED"}})))
        self.assertEqual((second["action"], second["values"][0]["key"], second["next_item"]), ("NEXT", "Q1.D03.on_medication", "Q1.D04"))

    def test_without_flow_it_invents_nothing(self):
        out = mr.MimicExtractor().extract(a_input("Q1.D01", "네", {}))
        self.assertEqual(out, {"action": "NEXT", "next_item": "END", "values": []})

    def test_item_without_slots_gives_no_values(self):
        ex = mr.MimicExtractor(ORDER, {}, SKIPS)
        for u in ("네", "잘 모르겠어요", "말하기 싫어요"):
            out = ex.extract(a_input("Q4", u, {}))
            self.assertEqual((out["action"], out["values"]), ("NEXT", []))
        self.assertEqual(ex.extract(a_input("Q4", "왜 물어보시나요", {}))["action"], "REDIRECT")

    def test_rules_are_data_not_code(self):
        rules = mr.load_mimic_rules()
        rules = copy.deepcopy(rules); rules["extractor"]["hold"] = ["유보합니다"]
        ex = mr.MimicExtractor(ORDER, SLOTS, SKIPS, rules=rules)
        self.assertEqual(ex.extract(a_input("Q4", "유보합니다", {}))["action"], "HOLD")
        self.assertNotEqual(mr.MimicExtractor(ORDER, SLOTS, SKIPS).extract(a_input("Q4", "유보합니다", {}))["action"], "HOLD")
        with TemporaryDirectory() as t:
            bad = Path(t) / "r.json"; bad.write_text(json.dumps({"extractor": {}, "responder": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                mr.load_mimic_rules(bad)


def _req(action, **kw):
    nxt = kw.pop("next", {"type": "NEXT", "question_id": "Q4", "text": "담배를 피우시나요?"})
    base = {"action": action, "topic": "흡연 여부", "next": nxt}
    if action != "START":
        base.update(user_utterance="네 피웠어요", known="흡연함")
    base.update(kw)
    return base


REQUESTS = [
    _req("START", next={"type": "NEXT", "question_id": "Q1.D01", "text": "뇌졸중으로 진단받으셨나요?"}),
    _req("NEXT"), _req("NEXT", known=""), _req("NEXT", next={"type": "END"}),
    _req("CORRECT"), _req("CORRECT", known="", next={"type": "END"}),
    _req("REASK", reason="MISSING", ask="담배를 피우는지", next={"type": "SAME", "question_id": "Q4", "text": "담배를 피우시나요?"}),
    _req("REASK", reason="VAGUE_QUANTITY", ask="며칠인지", next={"type": "SAME", "question_id": "Q8_1", "text": "며칠 하셨나요?"}),
    _req("REASK", reason="MISSING_NUMBER", ask="며칠인지", next={"type": "SAME", "question_id": "Q8_1", "text": "며칠 하셨나요?"}),
    _req("CONFIRM", reason="CONFLICT_WITH_PREVIOUS", known="", ask="담배를 피우는지", previous="피움", current="안 피움",
         next={"type": "SAME", "question_id": "Q4", "text": "담배를 피우시나요?"}),
    _req("HOLD", reason="UNCERTAIN", hold="흡연 여부"), _req("HOLD", reason="UNCERTAIN", hold="흡연 여부", next={"type": "END"}),
    _req("SKIP", reason="REQUEST_SKIP", hold="흡연 여부"), _req("SKIP", reason="REFUSED", hold="흡연 여부", next={"type": "END"}),
    _req("REDIRECT", reason="ASK_PURPOSE", next={"type": "SAME", "question_id": "Q4", "text": "담배를 피우시나요?"}),
    _req("REDIRECT", reason="OFF_TOPIC", next={"type": "SAME", "question_id": "Q4", "text": "담배를 피우시나요?"}),
    _req("REDIRECT", reason="OUT_OF_SCHEMA", note="알레르기가 있다고 말함", next={"type": "SAME", "question_id": "Q4", "text": "담배를 피우시나요?"}),
]


class Responder(Env):
    def test_every_request_gives_valid_deterministic_korean_text(self):
        r = mr.MimicResponder()
        seen = set()
        for req in REQUESTS:
            assert_valid("ResponseRequest", req)                     # 입력이 계약을 지키는 것부터 확인한다
            text = r.respond(req)
            self.assertEqual(list(validator("ResponseText").iter_errors(text)), [], req)
            self.assertEqual(text, r.respond(copy.deepcopy(req)), "결정적")
            self.assertRegex(text, r"[가-힣]{2,}"); self.assertNotRegex(text, r"[{}]")
            self.assertEqual(self.session.respond(req).is_fallback, False, req)   # 세션 경로에서 대체 문장이 되지 않는다
            seen.add(text)
        self.assertGreaterEqual(len(seen), 16)                       # 동작별로 문장이 다르다

    def test_variety_for_the_same_action(self):
        r = mr.MimicResponder()
        outs = {r.respond(_req("NEXT", user_utterance=f"{i}일이요", known=f"{i}일")) for i in range(12)}
        self.assertGreaterEqual(len(outs), 3)

    def test_end_has_no_question_and_uses_known_material_only(self):
        r = mr.MimicResponder()
        self.assertNotIn("?", r.respond(_req("NEXT", next={"type": "END"})))
        t = r.respond(_req("REASK", reason="MISSING", ask="담배를 피우는지", next={"type": "SAME", "question_id": "Q4", "text": "Q문장"}))
        self.assertIn("담배를 피우는지", t)

    def test_no_clinical_advice_words_in_templates(self):
        rules = mr.load_mimic_rules()["responder"]
        banned = ["처방", "복용하세요", "드세요", "병원에 가", "진단입니다", "치료", "위험합니다", "괜찮을 것"]
        for k, lst in rules.items():
            if k.startswith("_"):
                continue
            for t in lst:
                for w in banned:
                    self.assertNotIn(w, t, (k, t))


class ServerSelectsMimic(unittest.TestCase):
    base = {"MODEL_RUNTIME_BACKEND": "mimic", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}

    def test_needs_allow_fake_like_fake(self):
        with self.assertRaises(SystemExit):
            server.build_service(dict(self.base))

    def test_bad_flow_file_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            server.build_service({**self.base, "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_MIMIC_FLOW_FILE": "/nonexistent.json"})

    def test_info_reports_backend_mimic_and_extract_respond_work_over_http(self):
        import threading
        from test_server import start
        with TemporaryDirectory() as t:
            flow = Path(t) / "flow.json"; flow.write_text(json.dumps(FLOW, ensure_ascii=False), encoding="utf-8")
            StubReader.backend_cls = mr.MimicBackend
            self.addCleanup(setattr, StubReader, "backend_cls", mr.FakeBackend)
            srv, url, svc, _ = start(t, MODEL_RUNTIME_BACKEND="mimic", MODEL_RUNTIME_FAKE_FLOW_FILE=str(flow))   # 가짜와 같은 변수로도 흐름 파일을 받는다
            self.addCleanup(srv.shutdown); self.addCleanup(svc.close)
            info = call(url, "/v1/info")[1]
            self.assertEqual(info["backend"], "mimic")
            self.assertEqual(info["serving_config_expectation"]["constraint"]["backend"], "mimic")
            self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0] in (200, 202), True)
            wait_until(lambda: svc.releases.get("r1") is not None and svc.releases["r1"].state == "LOADED")
            st, body, hdr = call(url, "/v1/releases/r1/extract", extract_body(a_input("Q4", "아니요 안 피웠어요", {}), TABLE))
            self.assertEqual((st, body["next_item"], body["values"][0]["value"]), (200, "Q5", False))
            st, text, _ = call(url, "/v1/releases/r1/respond", B_INPUT)
            self.assertEqual(st, 200); self.assertRegex(text, r"[가-힣]")


if __name__ == "__main__":
    unittest.main()
