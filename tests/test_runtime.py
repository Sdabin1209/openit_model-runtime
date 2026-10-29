import copy
import json
import logging
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import rules_for, A_INPUT, B_INPUT, B_START, B_END, BAD_A, GOOD_A, GOOD_B, GOOD_EXTRACTION, SCHEMA_DIR, assert_valid, cfgs_and_hashes

import model_runtime as mr
from boundary_contracts.canonical import canonicalize, sha256_hash


def make(script=None, features=None, **over):
    kw = {"supported_features": features} if features else {}
    b = mr.FakeBackend.scripted(script, **kw) if script else mr.FakeBackend(**kw)
    cfgs, hashes = cfgs_and_hashes(b, over, over)
    return b, cfgs, mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hashes, backend=b)


class Canonical(unittest.TestCase):
    def test_serving_config_hash_is_reference_hash(self):
        cfg = mr.fake_serving_config()
        self.assertEqual(mr.serving_config_hash(cfg), sha256_hash(cfg))
        self.assertEqual(canonicalize({"b": 1, "a": [1.0, 0.5, "한"]}), '{"a":[1,0.5,"한"],"b":1}'.encode())

    def test_unnormalizable_config_is_contract_violation(self):
        cfg = mr.fake_serving_config()
        cfg["decoding"]["top_p"] = float("nan")
        with self.assertRaises(mr.ContractViolation):
            mr.validate_serving_config(cfg)


class Config(unittest.TestCase):
    def test_hash_stable_and_sensitive(self):
        cfg = mr.fake_serving_config()
        h = mr.serving_config_hash(cfg)
        self.assertRegex(h, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(h, mr.serving_config_hash(dict(reversed(list(cfg.items())))))
        c2 = copy.deepcopy(cfg); c2["decoding"]["seed"] += 1
        self.assertNotEqual(h, mr.serving_config_hash(c2))

    def test_validation(self):
        cfg = mr.fake_serving_config()
        for mutate in (lambda c: c.pop("max_attempts"), lambda c: c.update(extra=1),
                       lambda c: c.update(max_attempts=0), lambda c: c["decoding"].pop("seed"),
                       lambda c: c["decoding"].update(temperature=-1), lambda c: c.pop("protection_policy_version")):
            c = copy.deepcopy(cfg); mutate(c)
            with self.assertRaises(mr.ContractViolation):
                mr.validate_serving_config(c)
        mr.validate_serving_config(cfg)


class SchemaDir(unittest.TestCase):
    def test_version_read_from_parent_and_mismatch_rejected(self):
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        with TemporaryDirectory() as t:
            shutil.copytree(SCHEMA_DIR.parent, Path(t) / "schema")
            sd = Path(t) / "schema" / "schemas"
            (Path(t) / "schema" / "CONTRACT_VERSION").write_text("2\n")
            with self.assertRaises(mr.ContractVersionMismatch):
                mr.FakeModelSession(cfgs, schema_dir=sd, expected_serving_config_hashes=hs, backend=b)
            (Path(t) / "schema" / "CONTRACT_VERSION").unlink()
            with self.assertRaises(mr.ContractVersionMismatch):
                mr.FakeModelSession(cfgs, schema_dir=sd, expected_serving_config_hashes=hs, backend=b)

    def test_schema_root_is_not_accepted_as_schema_dir(self):
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        with self.assertRaises(Exception):
            mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR.parent, expected_serving_config_hashes=hs, backend=b)

    def test_fixture_matches_workspace_schema(self):
        ws = Path(__file__).resolve().parents[2] / "survey-service" / "schema"
        if not ws.is_dir():
            self.skipTest("작업공간 스키마 없음")
        self.assertEqual((ws / "CONTRACT_VERSION").read_bytes(), (SCHEMA_DIR.parent / "CONTRACT_VERSION").read_bytes())
        self.assertEqual(list(SCHEMA_DIR.glob("*.json")), [])   # 모델 입출력 스키마의 복사본을 두지 않는다(boundary_contracts 패키지를 쓴다)


def prompts_in(tmp, texts=None):
    """어댑터 디렉터리 두 개와 각 디렉터리 안의 시스템 프롬프트 파일, SystemPromptRef를 만든다."""
    from boundary_contracts.canonical import file_hash
    texts = texts or {"LORA_A": "A용 시스템 프롬프트", "LORA_B": "B용 시스템 프롬프트"}
    ad, refs = {}, {}
    for r, t in texts.items():
        d = Path(tmp) / r
        d.mkdir(parents=True, exist_ok=True)
        (d / "system_prompt.txt").write_text(t, encoding="utf-8")
        ad[r], refs[r] = d, {"path": "system_prompt.txt", "sha256": file_hash(d / "system_prompt.txt")}
    return ad, refs


class Samples(unittest.TestCase):
    def test_test_inputs_are_contract_valid(self):
        """시험 입력이 boundary_contracts 패키지의 스키마를 통과한다(복사본이 아니라 패키지가 기준)."""
        assert_valid("ActionExtractionInput", A_INPUT)
        for r in (B_INPUT, B_START, B_END):
            assert_valid("ResponseRequest", r)
        assert_valid("ActionExtraction", GOOD_EXTRACTION)
        assert_valid("ResponseText", GOOD_B)


class Load(unittest.TestCase):
    def setUp(self):
        self.b = mr.FakeBackend()
        self.cfgs, self.hs = cfgs_and_hashes(self.b)
        self.kw = dict(schema_dir=SCHEMA_DIR, backend=self.b)

    def fake(self, cfgs=None, hs=None):
        return mr.FakeModelSession(cfgs or self.cfgs, expected_serving_config_hashes=hs or self.hs, **self.kw)

    def test_hash_mismatch_names_the_role(self):
        for role in ("LORA_A", "LORA_B"):
            with self.subTest(role=role):
                bad = dict(self.hs, **{role: "sha256:" + "0" * 64})
                with self.assertRaises(mr.ServingConfigMismatch) as cm:
                    self.fake(hs=bad)
                self.assertEqual(cm.exception.role, role)
                self.assertIn(role, str(cm.exception))

    def test_only_one_role_drifting_is_detected(self):
        cfgs = copy.deepcopy(self.cfgs); cfgs["LORA_B"]["decoding"]["temperature"] = 0.7   # 해시는 옛 값
        with self.assertRaises(mr.ServingConfigMismatch) as cm:
            self.fake(cfgs=cfgs)
        self.assertEqual(cm.exception.role, "LORA_B")

    def test_role_sets_must_match(self):
        with TemporaryDirectory() as d:
            ad, refs = prompts_in(d)
            base = dict(schema_dir=SCHEMA_DIR, backend=self.b, system_prompts=refs)
            cases = {
                "어댑터 하나 부족": ({"LORA_A": ad["LORA_A"]}, self.cfgs, self.hs, refs),
                "설정 하나 부족": (ad, {"LORA_A": self.cfgs["LORA_A"]}, self.hs, refs),
                "해시 하나 부족": (ad, self.cfgs, {"LORA_A": self.hs["LORA_A"]}, refs),
                "모르는 역할": (ad, {**self.cfgs, "LORA_C": self.cfgs["LORA_A"]}, self.hs, refs),
                "역할 어긋남": (ad, self.cfgs, {"LORA_A": self.hs["LORA_A"], "LORA_X": self.hs["LORA_B"]}, refs),
                "프롬프트 하나 부족": (ad, self.cfgs, self.hs, {"LORA_A": refs["LORA_A"]}),
            }
            for name, (a, c, h, p) in cases.items():
                with self.subTest(name):
                    with self.assertRaises(mr.ContractViolation):
                        mr.load(a, c, expected_serving_config_hashes=h, **{**base, "system_prompts": p})
            with self.assertRaises(mr.ContractViolation):
                self.fake(cfgs={"LORA_A": self.cfgs["LORA_A"]})

    def test_unsupported_names_the_role(self):
        for role in ("LORA_A", "LORA_B"):
            for key, val in (("prompt_template_version", "prompt-2"), ("output_contract", 2),
                             ("protection_policy_version", "protect-1")):   # v1의 prompt-2, protect-1은 더 이상 지원하지 않는다
                with self.subTest(role=role, key=key):
                    cfgs, hs = cfgs_and_hashes(self.b, *([{key: val}, None] if role == "LORA_A" else [None, {key: val}]))
                    with self.assertRaises(mr.UnsupportedServingConfig) as cm:
                        self.fake(cfgs, hs)
                    self.assertIn(role, str(cm.exception))
        cfgs = copy.deepcopy(self.cfgs); cfgs["LORA_B"]["constraint"]["backend"] = "other"
        with self.assertRaises(mr.UnsupportedServingConfig):
            self.fake(cfgs, {r: mr.serving_config_hash(c) for r, c in cfgs.items()})

    def test_load_adapters_and_package_system_prompt(self):
        with TemporaryDirectory() as d:
            ad, refs = prompts_in(d)
            s = mr.load(ad, self.cfgs, expected_serving_config_hashes=self.hs, system_prompts=refs, **self.kw)
            self.assertEqual(s.serving_config_hashes, self.hs)
            self.assertEqual(s.system_prompt_hashes["LORA_A"], refs["LORA_A"]["sha256"])   # 패키지 파일의 해시와 같다
            with self.assertRaises(mr.ContractViolation):
                mr.load({**ad, "LORA_B": Path(d) / "nope"}, self.cfgs, expected_serving_config_hashes=self.hs, system_prompts=refs, **self.kw)
            with self.assertRaises(mr.BackendNotAvailable):
                mr.load(ad, self.cfgs, expected_serving_config_hashes=self.hs, system_prompts=refs, schema_dir=SCHEMA_DIR)

    def test_prompt_comes_only_from_the_package_file(self):
        """서빙에 쓰는 시스템 프롬프트는 어댑터 디렉터리의 파일 내용이다(코드나 설정에 박은 문장이 아니다)."""
        with TemporaryDirectory() as d:
            ad, refs = prompts_in(d, {"LORA_A": "패키지-A-프롬프트", "LORA_B": "패키지-B-프롬프트"})
            b = mr.FakeBackend.scripted({"LORA_A": [GOOD_A], "LORA_B": [GOOD_B]})
            cfgs, hs = cfgs_and_hashes(b)
            s = mr.load(ad, cfgs, expected_serving_config_hashes=hs, system_prompts=refs, schema_dir=SCHEMA_DIR, backend=b)
            s.extract(A_INPUT, *rules_for(A_INPUT)); s.respond(B_INPUT)
            self.assertIn("<|im_start|>system\n패키지-A-프롬프트<|im_end|>", b.requests[0].prompt)
            self.assertIn("<|im_start|>system\n패키지-B-프롬프트<|im_end|>", b.requests[1].prompt)

    def test_system_prompt_file_problems_are_rejected(self):
        from boundary_contracts.canonical import file_hash
        with TemporaryDirectory() as d:
            ad, refs = prompts_in(d)
            kw = dict(expected_serving_config_hashes=self.hs, **self.kw)
            (ad["LORA_B"] / "system_prompt.txt").write_text("바뀐 프롬프트", encoding="utf-8")   # 해시와 다름
            with self.assertRaises(mr.SystemPromptMismatch) as cm:
                mr.load(ad, self.cfgs, system_prompts=refs, **kw)
            self.assertEqual(cm.exception.role, "LORA_B")
            ad, refs = prompts_in(d)
            for bad in ({"path": "nope.txt", "sha256": refs["LORA_A"]["sha256"]}, {"path": "../LORA_B/system_prompt.txt", "sha256": refs["LORA_B"]["sha256"]},
                        {"path": "system_prompt.txt", "sha256": "sha256:" + "0" * 64}):
                with self.subTest(bad=bad["path"]), self.assertRaises(mr.SystemPromptMismatch):
                    mr.load(ad, self.cfgs, system_prompts={**refs, "LORA_A": bad}, **kw)
            (ad["LORA_A"] / "system_prompt.txt").write_text("  \n", encoding="utf-8")   # 비어 있는 프롬프트
            with self.assertRaises(mr.SystemPromptMismatch):
                mr.load(ad, self.cfgs, system_prompts={**refs, "LORA_A": {"path": "system_prompt.txt", "sha256": file_hash(ad["LORA_A"] / "system_prompt.txt")}}, **kw)


class PerRole(unittest.TestCase):
    """역할마다 설정이 다를 수 있고(D45), 두 역할은 한 백엔드(같은 기반 모델 가중치)를 공유한다."""

    def test_roles_use_their_own_decoding_seed_attempts_and_share_one_backend(self):
        b = mr.FakeBackend.scripted({"LORA_A": ["x", GOOD_A], "LORA_B": ["", " ", GOOD_B]})
        cfgs, hs = cfgs_and_hashes(
            b, {"max_attempts": 2, "decoding": {"constraint_mode": "constrained", "temperature": 0, "seed": 10, "max_tokens": 64}},
            {"max_attempts": 3, "decoding": {"constraint_mode": "none", "temperature": 0.7, "seed": 500, "max_tokens": 256}})
        self.assertNotEqual(hs["LORA_A"], hs["LORA_B"])
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        s.extract(A_INPUT, *rules_for(A_INPUT)); s.respond(B_INPUT)
        a_reqs = [r for r in b.requests if r.role == "LORA_A"]; b_reqs = [r for r in b.requests if r.role == "LORA_B"]
        self.assertEqual([r.seed for r in a_reqs], [10, 11]); self.assertEqual([r.seed for r in b_reqs], [500, 501, 502])
        self.assertEqual(a_reqs[0].decoding["temperature"], 0); self.assertEqual(b_reqs[0].decoding["temperature"], 0.7)
        self.assertIsNotNone(a_reqs[0].constraint_schema); self.assertIsNone(b_reqs[0].constraint_schema)
        self.assertEqual(len(b.requests), 5)   # 한 백엔드 객체가 두 역할의 요청을 모두 받았다

    def test_b_is_never_constrained_even_when_config_says_constrained(self):
        """LoRA-B 출력은 JSON이 아니라 문장이라 제약 디코딩에 넘길 스키마가 없다."""
        b = mr.FakeBackend.scripted({"LORA_B": [GOOD_B]})
        cfgs, hs = cfgs_and_hashes(b)   # 기본 constraint_mode는 constrained
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        s.respond(B_INPUT)
        self.assertIsNone(b.requests[0].constraint_schema)

    def test_protection_policy_and_templates(self):
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        self.assertEqual(s._cfgs["LORA_A"]["protection_policy_version"], "protect-2")
        s.extract(A_INPUT, *rules_for(A_INPUT)); s.respond(B_INPUT)
        self.assertIn(mr.FAKE_SYSTEM_PROMPTS["LORA_A"], b.requests[0].prompt)
        self.assertIn(mr.FAKE_SYSTEM_PROMPTS["LORA_B"], b.requests[1].prompt)

    def test_backend_must_match_every_roles_constraint(self):
        b = mr.FakeBackend(); cfgs, _ = cfgs_and_hashes(b)
        cfgs["LORA_A"]["constraint"]["backend_version"] = "9"
        with self.assertRaises(mr.UnsupportedServingConfig):
            mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, backend=b,
                                expected_serving_config_hashes={r: mr.serving_config_hash(c) for r, c in cfgs.items()})


class Extract(unittest.TestCase):
    def test_first_attempt(self):
        _, _, s = make({"LORA_A": [GOOD_A]})
        self.assertEqual(s.extract(A_INPUT, *rules_for(A_INPUT)), GOOD_EXTRACTION)
        self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome, s.last_call_info.protections), (1, "ok", ()))

    def test_retry_then_success_same_prompt_seed_plus_attempt(self):
        b, cfg, s = make({"LORA_A": ["not json", BAD_A, GOOD_A]})
        s.extract(A_INPUT, *rules_for(A_INPUT))
        self.assertEqual(s.last_call_info.attempts, 3)
        self.assertEqual([r.seed for r in b.requests], [1000, 1001, 1002])
        self.assertEqual(len({r.prompt for r in b.requests}), 1)

    def test_never_invents_a_value_when_retries_are_used_up(self):
        """D75: 끝내 못 지키면 대체 출력도 고친 출력도 없이 ModelOutputInvalid다."""
        for script in (["x"], [BAD_A], ["[]"], ['{"values":[],"action":"NEXT"}'], ['{"values":[],"action":"NEXT","next_item":"END","x":1}'],
                       [json.dumps({**GOOD_EXTRACTION, "action": "NEXT"})]):   # NEXT에 reason·targets가 붙으면 위반
            with self.subTest(script=script[0][:30]):
                b, _, s = make({"LORA_A": script}, max_attempts=2)
                with self.assertRaises(mr.ModelOutputInvalid) as cm:
                    s.extract(A_INPUT, *rules_for(A_INPUT))
                self.assertEqual(cm.exception.attempts, 2)
                self.assertEqual(len(b.requests), 2)
                self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome), (2, "invalid"))

    def test_input_violation_before_model(self):
        b, _, s = make()
        for bad in ({"question_id": "Q1"}, B_INPUT, {**A_INPUT, "extra": 1}, {**A_INPUT, "user_utterance": ""}):
            with self.assertRaises(mr.ContractViolation):
                s.extract(bad, *rules_for(bad))
        self.assertEqual(b.requests, [])

    def test_unconstrained_mode_still_validates(self):
        dec = {"constraint_mode": "none", "temperature": 0, "seed": 1, "max_tokens": 64}
        b, _, s = make({"LORA_A": [BAD_A, GOOD_A]}, decoding=dec)
        s.extract(A_INPUT, *rules_for(A_INPUT))
        self.assertIsNone(b.requests[0].constraint_schema)
        self.assertEqual(s.last_call_info.attempts, 2)

    def test_raw_reports_validity_without_raising(self):
        _, _, s = make({"LORA_A": [BAD_A]}, max_attempts=1)
        r = s.extract_raw(A_INPUT)
        self.assertFalse(r.valid); self.assertIsInstance(r.output, dict); self.assertTrue(r.errors)
        self.assertEqual(s.last_call_info.outcome, "raw")
        _, _, s = make({"LORA_A": ["아님"]}, max_attempts=1)
        r = s.extract_raw(A_INPUT)
        self.assertEqual((r.output, r.valid, r.errors), (None, False, ("json",)))

    def test_nan_is_rejected(self):
        _, _, s = make({"LORA_A": ['{"values":[{"key":"Q10.days","value":NaN}],"action":"NEXT","next_item":"END"}']}, max_attempts=1)
        with self.assertRaises(mr.ModelOutputInvalid):
            s.extract(A_INPUT, *rules_for(A_INPUT))


class Respond(unittest.TestCase):
    def test_first_attempt_returns_text_as_is(self):
        _, _, s = make({"LORA_B": ["  " + GOOD_B + "\n"]})
        r = s.respond(B_INPUT)
        self.assertEqual(r, mr.ResponseResult(GOOD_B, False))   # 앞뒤 공백만 뗀다
        self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome), (1, "ok"))

    def test_start_and_end_inputs(self):
        _, _, s = make({"LORA_B": [GOOD_B]})
        self.assertFalse(s.respond(B_START).is_fallback)
        self.assertFalse(s.respond(B_END).is_fallback)

    def test_retry_then_success(self):
        b, _, s = make({"LORA_B": ["", "   ", GOOD_B]})
        self.assertEqual(s.respond(B_INPUT).text, GOOD_B)
        self.assertEqual(s.last_call_info.attempts, 3)
        self.assertEqual([r.seed for r in b.requests], [1000, 1001, 1002])

    def test_fallback_is_built_from_materials_and_is_marked(self):
        _, _, s = make({"LORA_B": [""]}, max_attempts=2)
        r = s.respond(B_INPUT)
        self.assertTrue(r.is_fallback)
        self.assertEqual(r.text, "진단 받음 지금 고혈압 약을 먹는지 다음은 고혈압입니다. 진단받은 적이 있으신지, 지금 약을 드시는지 알려 주세요.")
        self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome, s.last_call_info.protections),
                         (2, "fallback", (("G0", 1), ("G5", 1))))
        assert_valid("ResponseText", r.text)

    def test_input_violation_before_model(self):
        b, _, s = make()
        for bad in ({"action": "NEXT"}, A_INPUT, {**B_INPUT, "action": "REASK", "ask": ""}):
            with self.assertRaises(mr.ContractViolation):
                s.respond(bad)
        self.assertEqual(b.requests, [])

    def test_raw(self):
        _, _, s = make({"LORA_B": [""]}, max_attempts=1)
        r = s.respond_raw(B_INPUT)
        self.assertEqual((r.output, r.valid), ("", False))


class Fallback(unittest.TestCase):
    def test_pure_and_materials_only(self):
        a = mr.fallback_text(B_INPUT)
        self.assertEqual(a, mr.fallback_text(dict(reversed(list(B_INPUT.items())))))
        self.assertNotIn(B_INPUT["user_utterance"], a)   # 발화는 쓰지 않는다
        self.assertEqual(mr.fallback_text(B_START), B_START["next"]["text"])

    def test_consecutive_duplicates_once_and_empty_materials(self):
        self.assertEqual(mr.fallback_text({**B_INPUT, "known": "같은 말", "ask": "같은 말"}).split(" 다음은")[0], "같은 말")
        empty = mr.PROTECTION_POLICIES[mr.PROTECTION_POLICY_VERSION].empty_fallback_text
        self.assertEqual(mr.fallback_text(B_END | {"known": ""}), empty)   # END이고 known이 비면 재료가 없다(문구는 잠정, 미정)
        self.assertEqual(mr.fallback_text(B_END), "최근 일주일 2일")

    def test_unknown_policy_version_rejected(self):
        with self.assertRaises(KeyError):
            mr.fallback_text(B_INPUT, policy_version="protect-1")


class Relax(unittest.TestCase):
    def test_action_extraction_relaxed_misses_rule_but_post_validation_catches(self):
        import jsonschema
        b, _, s = make({"LORA_A": [BAD_A, GOOD_A]})
        schema = s._schemas.model_output_schema("LORA_A")
        self.assertNotIn("$comment", repr(schema))
        relaxed, removed = mr.relax(schema, b.supported_features)
        for kw in ("'allOf'", "'if'", "'then'", "'oneOf'", "'not'"):
            self.assertNotIn(kw, repr(relaxed))
        self.assertIn("allOf->anyOf", removed)    # relax-3(D83): anyOf 변형으로 펼쳐 제약에 들어간다
        bad = json.loads(BAD_A)
        self.assertFalse(jsonschema.Draft202012Validator(relaxed).is_valid(bad))
        # anyOf가 없는 엔진이면 예전처럼 allOf를 빼고, 그때는 제약만으로는 놓친다
        old, old_removed = mr.relax(schema, ["type", "properties", "required", "enum", "items", "additionalProperties"])
        self.assertIn("allOf", old_removed)
        self.assertTrue(jsonschema.Draft202012Validator(old).is_valid(bad))
        self.assertTrue(s._schemas.output_violations("LORA_A", bad))              # 사후 검증은 그대로 잡는다
        s.extract(A_INPUT, *rules_for(A_INPUT))
        self.assertEqual(s.last_call_info.attempts, 2)
        self.assertIn("allOf->anyOf", s.last_call_info.relaxed)

    def test_never_narrows(self):
        import jsonschema
        _, _, s = make()
        orig = s._schemas.model_output_schema("LORA_A")
        for feats in ([], ["type", "properties", "enum", "anyOf", "$defs", "$ref"], list(mr.DEFAULT_SUPPORTED_FEATURES)):
            v = jsonschema.Draft202012Validator(mr.relax(orig, feats)[0])
            for inst in (GOOD_EXTRACTION, {"values": [], "action": "NEXT", "next_item": "END"}, {"x": 1}, []):
                if jsonschema.Draft202012Validator(orig).is_valid(inst):
                    self.assertTrue(v.is_valid(inst), feats)

    def test_text_role_has_no_constraint_schema(self):
        _, _, s = make()
        self.assertEqual(s._roles["LORA_B"].constraint, (None, ()))

    def test_oneof_const_becomes_enum(self):
        r, removed = mr.relax({"oneOf": [{"const": "A"}, {"const": "B"}]}, ["enum"])
        self.assertEqual(r, {"enum": ["A", "B"]}); self.assertEqual(removed, ["oneOf->enum"])
        self.assertEqual(mr.relax({"oneOf": [{"type": "string"}]}, ["anyOf", "type"])[0],
                         {"anyOf": [{"type": "string"}]})


class Prompt(unittest.TestCase):
    def test_deterministic_chatml_without_thinking(self):
        cfg = mr.fake_serving_config()
        kw = dict(serving_config=cfg, system_prompt="시스템 문장", schema_dir=SCHEMA_DIR)
        a = mr.render_prompt("LORA_A", A_INPUT, **kw)
        b = mr.render_prompt("LORA_A", dict(reversed(list(A_INPUT.items()))), **kw)
        self.assertEqual(a, b)   # 최상위 키 순서와 무관(prompt-4: A의 최상위는 서버가 정렬, state 안은 호출자 순서)
        self.assertTrue(a.startswith("<|im_start|>system\n시스템 문장<|im_end|>\n<|im_start|>user\n{"))
        self.assertTrue(a.endswith("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"))   # enable_thinking=False
        self.assertIn("십 년 전에 고혈압 진단을 받았어요.", a)   # ensure_ascii=False
        self.assertIn(json.dumps(dict(sorted(A_INPUT.items())), ensure_ascii=False), a)   # prompt-4: 학습 글자(D77)
        with self.assertRaises(mr.UnsupportedServingConfig):   # prompt-3은 제거됨(CHANGELOG 참조)
            mr.render_prompt("LORA_A", A_INPUT, **{**kw, "serving_config": mr.fake_serving_config(prompt_template_version="prompt-3")})

    def test_errors(self):
        cfg = mr.fake_serving_config()
        kw = dict(serving_config=cfg, system_prompt="s", schema_dir=SCHEMA_DIR)
        with self.assertRaises(mr.UnsupportedServingConfig):
            mr.render_prompt("LORA_A", A_INPUT, **{**kw, "serving_config": dict(cfg, prompt_template_version="prompt-2")})
        with self.assertRaises(mr.ContractViolation):
            mr.render_prompt("LORA_B", A_INPUT, **kw)   # 역할과 입력이 다르다
        with self.assertRaises(mr.ContractViolation):
            mr.render_prompt("LORA_A", A_INPUT, **{**kw, "system_prompt": " "})
        with self.assertRaises(mr.ContractViolation):
            mr.render_prompt("LORA_C", A_INPUT, **kw)


class Privacy(unittest.TestCase):
    def test_no_utterance_in_exceptions_or_logs(self):
        secret = {**A_INPUT, "user_utterance": "비밀발화123"}
        _, _, s = make({"LORA_A": ['{"values":[],"action":"NEXT","next_item":"END","note":"비밀발화123"}'],
                        "LORA_B": ["  "]}, max_attempts=1)
        with self.assertLogs("model_runtime", level=logging.DEBUG) as logs:
            logging.getLogger("model_runtime").debug("start")
            with self.assertRaises(mr.ModelOutputInvalid) as cm:
                s.extract(secret, *rules_for(secret))
            raw = s.extract_raw(secret)
            r = s.respond({**B_INPUT, "user_utterance": "비밀발화123"})
        self.assertNotIn("비밀발화123", "\n".join(logs.output) + repr(s.last_call_info) + repr(raw.errors) + str(cm.exception) + repr(cm.exception.errors))
        self.assertNotIn("비밀발화123", r.text)
        with self.assertRaises(mr.ContractViolation) as cm:
            s.extract({**secret, "extra": "비밀발화123"}, *rules_for({**secret, "extra": "비밀발화123"}))
        self.assertNotIn("비밀발화123", str(cm.exception))


class RetryTemperature(unittest.TestCase):
    def test_retry_temperature_used_from_second_attempt_only(self):
        dec = {"constraint_mode": "constrained", "temperature": 0, "seed": 10, "max_tokens": 64, "retry_temperature": 0.7}
        b, cfgs, s = make({"LORA_A": ["x", "y", GOOD_A]}, decoding=dec)
        s.extract(A_INPUT, *rules_for(A_INPUT))
        self.assertEqual([r.decoding["temperature"] for r in b.requests], [0, 0.7, 0.7])
        self.assertEqual([r.seed for r in b.requests], [10, 11, 12])

    def test_absent_key_keeps_temperature(self):
        b, cfgs, s = make({"LORA_A": ["x", GOOD_A]})
        s.extract(A_INPUT, *rules_for(A_INPUT))
        self.assertEqual({r.decoding["temperature"] for r in b.requests}, {0})

    def test_invalid_value_rejected(self):
        cfg = mr.fake_serving_config(); cfg["decoding"]["retry_temperature"] = 3
        with self.assertRaises(mr.ContractViolation):
            mr.validate_serving_config(cfg)


class FakeDefaults(unittest.TestCase):
    def test_default_fake_outputs_pass_contract(self):
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        assert_valid("ActionExtraction", s.extract(A_INPUT, *rules_for(A_INPUT)))
        r = s.respond(B_INPUT)
        self.assertFalse(r.is_fallback)
        assert_valid("ResponseText", r.text)


if __name__ == "__main__":
    unittest.main()
