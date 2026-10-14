"""출력 보호(docs/rules.md)의 시험. 파서 P1~P6, 생성기 규칙 1~5를 규칙 문장 단위로 쓴다."""
import json
import unittest

from helpers import (GOOD_GEN, PARSER_INPUT, SCHEMA_DIR, SLOT, WHOLE, directive, gen_input, item, out, ITEM)

import model_runtime as mr


def session(script, n=1):
    b = mr.FakeBackend.scripted(script)
    cfg = mr.fake_serving_config(b, max_attempts=n)
    return mr.FakeModelSession(cfg, schema_dir=SCHEMA_DIR, expected_serving_config_hash=mr.serving_config_hash(cfg), backend=b)


def parse_one(model_item, guide_list=(), raw=None):
    s = session({"LORA_A": [raw if raw is not None else out(model_item)]})
    r = s.parse({**PARSER_INPUT, "guide_list": list(guide_list)})
    return s, r


class ParserProtection(unittest.TestCase):
    def check(self, model_item, expect_intent, expect_fields=None, rule=None, guide_list=()):
        s, r = parse_one(model_item, guide_list)
        it = r["items"][0]
        self.assertEqual(it["intent"], expect_intent)
        for k, v in (expect_fields or {}).items():
            self.assertEqual(it[k], v, k)
        if rule:
            self.assertEqual(dict(s.last_call_info.protections), {rule: 1})
            self.assertEqual(s.last_call_info.outcome, "repaired")
        return it

    # "형식을 못 지키면 빈 목록"
    def test_P1_format_broken_gives_empty_list(self):
        for raw in ("not json", "[]", '{"items":"x"}', '{"items":[1]}', '{"items":[],"x":1}',
                    out({"item_no": 0, "intent": "UNRECOGNIZED"}), out(item(source_text="")), out(item(target={"a": 1}))):
            with self.subTest(raw=raw):
                s, r = parse_one(None, raw=raw)
                self.assertEqual(r, {"items": []})
                self.assertEqual(dict(s.last_call_info.protections), {"P1": 1})
                self.assertEqual(s.last_call_info.outcome, "fallback")

    # "의도가 어휘 밖이면 인식불가로"
    def test_P2_intent_outside_vocabulary(self):
        self.check(item(intent="MADE_UP", target=SLOT, value="v", progress_op="WAIT", guide_id="g"), "UNRECOGNIZED",
                   {"target": None, "value": None, "progress_op": None, "guide_id": None}, "P2")

    # "진행 조작이 어휘 밖이거나"
    def test_P3_progress_op_outside_vocabulary(self):
        self.check(item(intent="PROGRESS_OP", progress_op="DANCE"), "UNRECOGNIZED", {"progress_op": None}, "P3")

    # "의도가 진행 조작인데 진행 조작이 없으면 인식불가로"
    def test_P3_progress_intent_without_op(self):
        self.check(item(intent="PROGRESS_OP"), "UNRECOGNIZED", None, "P3")

    def test_progress_op_in_vocabulary_is_kept(self):
        s, r = parse_one(item(intent="PROGRESS_OP", progress_op="WAIT"))
        self.assertEqual(r["items"][0]["progress_op"], "WAIT"); self.assertEqual(s.last_call_info.outcome, "ok")

    # "안내 항목 ID가 받은 목록 밖이거나 ... 무관(OUT_OF_SCOPE)으로"
    def test_P4_guide_id_outside_list(self):
        self.check(item(intent="GUIDE_REQUEST", guide_id="g9"), "OUT_OF_SCOPE", {"guide_id": None}, "P4",
                   [{"guide_id": "g1", "selection_criterion": "c"}])

    # "의도가 안내 요청인데 없으면 무관으로"
    def test_P4_guide_request_without_id(self):
        self.check(item(intent="GUIDE_REQUEST"), "OUT_OF_SCOPE", None, "P4", [{"guide_id": "g1", "selection_criterion": "c"}])

    def test_guide_id_in_list_is_kept(self):
        s, r = parse_one(item(intent="GUIDE_REQUEST", guide_id="g1"), [{"guide_id": "g1", "selection_criterion": "c"}])
        self.assertEqual(r["items"][0]["guide_id"], "g1"); self.assertEqual(s.last_call_info.outcome, "ok")

    # "답함·모름·거절의 대상을 정하지 못했거나 ... 인식불가로"
    def test_P5_no_target(self):
        for intent in ("ANSWERED_INTENT", "SAYS_UNKNOWN", "SAYS_REFUSAL"):
            with self.subTest(intent=intent):
                self.check(item(intent=intent, value="v"), "UNRECOGNIZED", {"value": None}, "P5")

    # "답함에 값이 없거나"
    def test_P5_answered_without_value(self):
        self.check(item(intent="ANSWERED_INTENT", target=SLOT), "UNRECOGNIZED", {"target": None}, "P5")

    # "답함·정정이 문항 전체에 값을 붙였으면 인식불가로"
    def test_P5_whole_question_with_value(self):
        for intent in ("ANSWERED_INTENT", "CORRECTION_MARKED"):
            with self.subTest(intent=intent):
                self.check(item(intent=intent, target=WHOLE, value="v"), "UNRECOGNIZED", {"target": None, "value": None}, "P5")

    def test_correction_of_whole_question_without_value_is_kept(self):
        s, r = parse_one(item(intent="CORRECTION_MARKED", target=WHOLE))
        self.assertEqual(r["items"][0]["target"], WHOLE); self.assertEqual(s.last_call_info.outcome, "ok")

    # "정정에 값은 있는데 대상이 없으면 값을 비워 값 없는 정정으로"
    def test_P6_correction_value_without_target(self):
        self.check(item(intent="CORRECTION_MARKED", value="v"), "CORRECTION_MARKED", {"value": None, "target": None}, "P6")

    # "의도를 바꿀 때는 대상·값·진행 조작·안내 항목 ID를 비운다" — P2~P5 모두에서 확인됨. 여기서는 한꺼번에.
    def test_changing_intent_clears_all_four_fields(self):
        self.check(item(intent="PROGRESS_OP", progress_op="NOPE", target=SLOT, value="v", guide_id="g1"),
                   "UNRECOGNIZED", {"target": None, "value": None, "progress_op": None, "guide_id": None}, "P3",
                   [{"guide_id": "g1", "selection_criterion": "c"}])

    # "대상이 문항 정의에 없는 항목은 고치지 않고 그대로 낸다"
    def test_target_not_in_question_definitions_is_left_alone(self):
        ghost = {"question_id": "NO_SUCH_Q", "repeat_key": "", "slot_id": "nope"}
        it = item(intent="ANSWERED_INTENT", target=ghost, value="v")
        s, r = parse_one(it)
        self.assertEqual(r["items"], [it]); self.assertEqual(s.last_call_info.outcome, "ok")

    def test_valid_items_are_untouched_and_others_repaired_together(self):
        good = item(item_no=0, intent="ANSWERED_INTENT", target=SLOT, value="v")
        bad = item(item_no=1, intent="SAYS_REFUSAL")
        s, r = parse_one(None, raw=out(good, bad))
        self.assertEqual(r["items"][0], good); self.assertEqual(r["items"][1]["intent"], "UNRECOGNIZED")
        self.assertEqual(dict(s.last_call_info.protections), {"P5": 1})

    def test_protection_applies_after_retries_are_used(self):
        bad = out(item(intent="PROGRESS_OP"))
        b = mr.FakeBackend.scripted({"LORA_A": [bad, bad, bad]})
        cfg = mr.fake_serving_config(b)
        s = mr.FakeModelSession(cfg, schema_dir=SCHEMA_DIR, expected_serving_config_hash=mr.serving_config_hash(cfg), backend=b)
        self.assertEqual(s.parse(PARSER_INPUT)["items"][0]["intent"], "UNRECOGNIZED")
        self.assertEqual(s.last_call_info.attempts, 3); self.assertEqual(len(b.requests), 3)

    def test_protected_output_always_passes_schema(self):
        s = mr.SchemaSet(SCHEMA_DIR)
        for raw in ("x", out(item(intent="Z")), out(item(intent="ANSWERED_INTENT")), '{"items":[null]}'):
            _, r = parse_one(None, raw=raw)
            self.assertEqual(s.output_violations("LORA_A", r), [])

    def test_raw_reports_pre_protection_output(self):
        bad = out(item(intent="SAYS_REFUSAL"))
        s = session({"LORA_A": [bad]})
        rr = s.parse_raw(PARSER_INPUT)
        self.assertFalse(rr.valid); self.assertEqual(rr.output["items"][0]["intent"], "SAYS_REFUSAL")
        self.assertEqual(rr.errors, ("rule:P5",)); self.assertEqual(s.last_call_info.outcome, "raw")
        self.assertTrue(session({"LORA_A": [out(ITEM)]}).parse_raw(PARSER_INPUT).valid)
        self.assertIsNone(session({"LORA_A": ["nope"]}).parse_raw(PARSER_INPUT).output)


def gen(inp, text, n=1):
    s = session({"LORA_B": [json.dumps({"response_text": text}, ensure_ascii=False)]}, n)
    return s, s.generate(inp)


Q = lambda qid, t: {"question_id": qid, "sentence": t}
FIX = lambda k, t: {"key": k, "sentence": t}


class GeneratorProtection(unittest.TestCase):
    # 규칙 1: "응답 지시에 없는 값·문항을 말하지 않는다. ... 그 안의 값을 기록된 값처럼 말하지 않는다."
    def test_rule1_value_not_in_directive_triggers_fallback(self):
        inp = gen_input("ASK_NEXT", utterance="39도예요", targets=[SLOT],
                        sm={"question_sentences": [Q("Q1", "두통은 언제부터인가요?")]})
        s, r = gen(inp, "39도로 기록했습니다. 두통은 언제부터인가요?")
        self.assertTrue(r["is_fallback"]); self.assertEqual(r["response_text"], "두통은 언제부터인가요?")
        self.assertIn(("G1", 1), s.last_call_info.protections); self.assertIn(("G5", 1), s.last_call_info.protections)

    def test_rule1_value_from_directive_is_allowed(self):
        sv = {"slot": SLOT, "question_name": "체온", "value": {"amount": 38.5, "unit": "도"}, "role": "JUST_SAVED"}
        inp = gen_input("ASK_NEXT", utterance="38.5도", say_values=[sv], targets=[SLOT],
                        sm={"question_sentences": [Q("Q1", "다음 질문입니다.")]})
        _, r = gen(inp, "체온 38.5도로 알겠습니다. 다음 질문입니다.")
        self.assertFalse(r["is_fallback"])

    def test_rule1_number_only_in_utterance_is_not_allowed(self):
        inp = gen_input("ASK_NEXT", utterance="3일 됐어요", targets=[SLOT], sm={"question_sentences": [Q("Q1", "언제부터인가요?")]})
        _, r = gen(inp, "3일이시군요. 언제부터인가요?")
        self.assertTrue(r["is_fallback"])

    # 규칙 2, 3: 기계로 확인하지 못한다. 프롬프트에 규칙이 들어 있는지와, 대체 문장이 응답 지시의 행동을 바꾸지 않는지를 본다.
    def test_rule2_is_in_prompt_only(self):
        p = mr.render_prompt("LORA_B", gen_input(), serving_config=mr.fake_serving_config(), schema_dir=SCHEMA_DIR)
        self.assertIn("도와드리기 어렵다고만 한다", p)
        self.assertNotIn("is_fallback", p)

    def test_rule3_fallback_for_redirect_keeps_action_notice_then_awaiting_question(self):
        aw = {"action": "ASK_NEXT", "targets": [SLOT], "target_pending_id": None, "say_values": []}
        inp = gen_input("REDIRECT", utterance="오늘 날씨는?", awaiting=aw,
                        sm={"fixed_sentences": [FIX("OOS_NOTICE", "그 부분은 도와드리기 어렵습니다.")],
                            "question_sentences": [Q("Q1", "두통은 언제부터인가요?")]})
        _, r = gen(inp, "")  # 빈 출력은 형식 위반
        self.assertTrue(r["is_fallback"])
        self.assertEqual(r["response_text"], "그 부분은 도와드리기 어렵습니다. 두통은 언제부터인가요?")
        self.assertNotIn("날씨", r["response_text"])

    # 규칙 4: "행동이 위험 안내면 발화를 받아 주지 않고 문장 재료와 알릴 것대로만 말한다."
    def test_rule4_risk_notice_must_contain_material_sentence(self):
        inp = gen_input("RISK_NOTICE", utterance="가슴이 아파요",
                        sm={"fixed_sentences": [FIX("RISK_NOTICE", "직원을 불러 드리겠습니다.")]})
        s, r = gen(inp, "가슴이 아프시군요. 걱정하지 마세요.")
        self.assertTrue(r["is_fallback"]); self.assertEqual(r["response_text"], "직원을 불러 드리겠습니다.")
        self.assertIn(("G4", 1), s.last_call_info.protections)
        _, ok = gen(inp, "직원을 불러 드리겠습니다.")
        self.assertFalse(ok["is_fallback"])

    # 규칙 5: "위를 지키지 못하면 문장 재료를 그대로 읽는 문장(말할 값·알릴 것은 이름·값을 그대로 넣음)을 내고 is_fallback = true"
    def test_rule5_fallback_reads_materials_with_names_and_values(self):
        sv = {"slot": SLOT, "question_name": "체온", "value": {"amount": 38.5, "unit": "도"}, "role": "PENDING"}
        notice = {"kind": "CORRECTION_ACK", "guide_id": None, "question_id": "Q1", "question_name": "두통",
                  "slot": SLOT, "previous_value": "약함", "value": "심함"}
        inp = gen_input("ASK_CONFIRM", notices=[notice], say_values=[sv], sm={"fixed_sentences": [FIX("ASK_CONFIRM", "맞으신가요?")]})
        _, r = gen(inp, "")
        self.assertEqual(r, {"response_text": "두통: 심함 체온: 38.5도 맞으신가요?", "is_fallback": True})

    def test_rule5_fallback_is_deterministic_and_ignores_utterance(self):
        base = dict(targets=[SLOT], sm={"question_sentences": [Q("Q1", "언제부터인가요?")]})
        a = mr.fallback_text(gen_input("ASK_NEXT", utterance="가", **base))
        b = mr.fallback_text(gen_input("ASK_NEXT", utterance="나", **base))
        self.assertEqual((a, b), ("언제부터인가요?", "언제부터인가요?"))

    def test_fallback_each_action_uses_its_material(self):
        fixed_actions = ["ASK_REPEAT", "CALL_STAFF", "FAREWELL", "ASK_CONFIRM", "ASK_CORRECTION_TARGET", "ASK_FINISH",
                         "RISK_NOTICE", "WAIT_NOTICE", "ASK_STOP", "STAFF_NOTICE"]
        for a in fixed_actions:
            with self.subTest(action=a):
                self.assertEqual(mr.fallback_text(gen_input(a, sm={"fixed_sentences": [FIX(a, f"{a}문장")]})), f"{a}문장")
        refusal = gen_input("ASK_AFTER_REFUSAL", targets=[SLOT], sm={"refusal_explanations": [Q("Q1", "필요한 질문입니다.")],
                                                                    "question_sentences": [Q("Q1", "다시 여쭙니다.")]})
        self.assertEqual(mr.fallback_text(refusal), "필요한 질문입니다. 다시 여쭙니다.")
        reask = gen_input("ASK_MISSING", targets=[SLOT], sm={"reask_sentences": [{"slot": SLOT, "sentence": "단위를 말씀해 주세요."}],
                                                            "question_sentences": [Q("Q1", "체온은요?")],
                                                            "choices": [{"slot": SLOT, "choices": ["가", "나"]}]})
        self.assertEqual(mr.fallback_text(reask), "단위를 말씀해 주세요. 가, 나")
        guide = gen_input("ASK_NEXT", notices=[{"kind": "GUIDE", "guide_id": "g1", "question_id": None, "question_name": None,
                                                "slot": None, "previous_value": None, "value": None}],
                          sm={"guide_sentences": [{"guide_id": "g1", "question_id": None, "sentence": "안내입니다."}]})
        self.assertEqual(mr.fallback_text(guide), "안내입니다.")

    def test_fallback_with_no_materials_uses_default_sentence(self):
        self.assertEqual(mr.fallback_text(gen_input("ASK_NEXT")), "죄송합니다. 다시 한 번 말씀해 주세요.")

    def test_normal_output_is_not_fallback(self):
        _, r = gen(GENERATOR_INPUT_SIMPLE, "알겠습니다.")
        self.assertEqual(r, {"response_text": "알겠습니다.", "is_fallback": False})

    def test_invalid_shapes_fall_back_after_all_retries(self):
        s = session({"LORA_B": ["x", '{"response_text":""}', '{"text":"a"}']}, 3)
        r = s.generate(GENERATOR_INPUT_SIMPLE)
        self.assertTrue(r["is_fallback"]); self.assertEqual(s.last_call_info.attempts, 3)
        self.assertEqual(s.last_call_info.protections, (("G0", 1), ("G5", 1)))

    def test_raw_generator(self):
        s = session({"LORA_B": ['{"response_text":"x","is_fallback":true}']})
        rr = s.generate_raw(GENERATOR_INPUT_SIMPLE)
        self.assertFalse(rr.valid); self.assertEqual(rr.output["response_text"], "x")


GENERATOR_INPUT_SIMPLE = gen_input()


if __name__ == "__main__":
    unittest.main()
