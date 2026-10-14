import copy
import logging
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import BAD_PARSE, GENERATOR_INPUT, GOOD_GEN, GOOD_PARSE, PARSER_INPUT, SCHEMA_DIR

import model_runtime as mr
from boundary_contracts.canonical import canonicalize, sha256_hash


def make(script=None, features=None, **over):
    kw = {"supported_features": features} if features else {}
    b = mr.FakeBackend.scripted(script, **kw) if script else mr.FakeBackend(**kw)
    cfg = mr.fake_serving_config(b, **over)
    return b, cfg, mr.FakeModelSession(cfg, schema_dir=SCHEMA_DIR,
                                       expected_serving_config_hash=mr.serving_config_hash(cfg), backend=b)


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
        b = mr.FakeBackend(); cfg = mr.fake_serving_config(b); h = mr.serving_config_hash(cfg)
        mr.FakeModelSession(cfg, schema_dir=SCHEMA_DIR, expected_serving_config_hash=h, backend=b)
        with TemporaryDirectory() as t:
            shutil.copytree(SCHEMA_DIR.parent, Path(t) / "schema")
            (Path(t) / "schema" / "CONTRACT_VERSION").write_text("2\n")
            with self.assertRaises(mr.ContractVersionMismatch):
                mr.FakeModelSession(cfg, schema_dir=Path(t) / "schema" / "schemas", expected_serving_config_hash=h, backend=b)
            (Path(t) / "schema" / "CONTRACT_VERSION").unlink()
            with self.assertRaises(mr.ContractVersionMismatch):
                mr.FakeModelSession(cfg, schema_dir=Path(t) / "schema" / "schemas", expected_serving_config_hash=h, backend=b)

    def test_schema_root_is_not_accepted_as_schema_dir(self):
        b = mr.FakeBackend(); cfg = mr.fake_serving_config(b)
        with self.assertRaises(Exception):
            mr.FakeModelSession(cfg, schema_dir=SCHEMA_DIR.parent, expected_serving_config_hash=mr.serving_config_hash(cfg), backend=b)

    def test_fixture_matches_workspace_schema(self):
        ws = Path(__file__).resolve().parents[2] / "survey-service" / "schema"
        if not ws.is_dir():
            self.skipTest("작업공간 스키마 없음")
        self.assertEqual((ws / "CONTRACT_VERSION").read_bytes(), (SCHEMA_DIR.parent / "CONTRACT_VERSION").read_bytes())
        for f in SCHEMA_DIR.glob("*.json"):
            self.assertEqual((ws / "schemas" / f.name).read_bytes(), f.read_bytes(), f.name)


class Load(unittest.TestCase):
    def test_mismatch_and_unsupported(self):
        b = mr.FakeBackend(); cfg = mr.fake_serving_config(b); h = mr.serving_config_hash(cfg)
        kw = dict(schema_dir=SCHEMA_DIR, backend=b)
        with self.assertRaises(mr.ServingConfigMismatch):
            mr.FakeModelSession(cfg, expected_serving_config_hash="sha256:" + "0" * 64, **kw)
        for c in (dict(cfg, prompt_template_version="prompt-99"), dict(cfg, output_contract=2),
                  dict(cfg, protection_policy_version="protect-0")):
            with self.assertRaises(mr.UnsupportedServingConfig):
                mr.FakeModelSession(c, expected_serving_config_hash=mr.serving_config_hash(c), **kw)
        c = copy.deepcopy(cfg); c["constraint"]["backend"] = "other"
        with self.assertRaises(mr.UnsupportedServingConfig):
            mr.FakeModelSession(c, expected_serving_config_hash=mr.serving_config_hash(c), **kw)

    def test_load_adapters(self):
        b = mr.FakeBackend(); cfg = mr.fake_serving_config(b); h = mr.serving_config_hash(cfg)
        with TemporaryDirectory() as d:
            ad = {"LORA_A": Path(d), "LORA_B": Path(d)}
            kw = dict(schema_dir=SCHEMA_DIR, expected_serving_config_hash=h)
            self.assertEqual(mr.load(ad, cfg, backend=b, **kw).serving_config_hash, h)
            with self.assertRaises(mr.ContractViolation):
                mr.load({"LORA_A": Path(d)}, cfg, backend=b, **kw)
            with self.assertRaises(mr.BackendNotAvailable):
                mr.load(ad, cfg, **kw)


class Paths(unittest.TestCase):
    def test_first_attempt(self):
        _, _, s = make({"LORA_A": [GOOD_PARSE], "LORA_B": [GOOD_GEN]})
        self.assertEqual(s.parse(PARSER_INPUT)["items"][0]["intent"], "UNRECOGNIZED")
        self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome), (1, "ok"))
        self.assertEqual(s.generate(GENERATOR_INPUT), {"response_text": "알겠습니다.", "is_fallback": False})

    def test_retry_then_success_same_prompt_seed_plus_attempt(self):
        b, cfg, s = make({"LORA_A": ["not json", BAD_PARSE, GOOD_PARSE]})
        s.parse(PARSER_INPUT)
        self.assertEqual(s.last_call_info.attempts, 3)
        self.assertEqual([r.seed for r in b.requests], [1000, 1001, 1002])
        self.assertEqual(len({r.prompt for r in b.requests}), 1)

    def test_input_violation_before_model(self):
        b, _, s = make()
        with self.assertRaises(mr.ContractViolation):
            s.parse({"utterance": "x"})
        self.assertEqual(b.requests, [])

    def test_unconstrained_mode_still_validates(self):
        dec = {"constraint_mode": "none", "temperature": 0, "seed": 1, "max_tokens": 64}
        b, _, s = make({"LORA_A": [BAD_PARSE, GOOD_PARSE]}, decoding=dec)
        s.parse(PARSER_INPUT)
        self.assertIsNone(b.requests[0].constraint_schema)
        self.assertEqual(s.last_call_info.attempts, 2)

    def test_model_may_not_emit_is_fallback(self):
        _, _, s = make({"LORA_B": ['{"response_text":"a","is_fallback":false}', GOOD_GEN]})
        self.assertFalse(s.generate(GENERATOR_INPUT)["is_fallback"])
        self.assertEqual(s.last_call_info.attempts, 2)

    def test_constraint_schema_for_generator_has_no_is_fallback(self):
        b, _, s = make({"LORA_B": [GOOD_GEN]})
        s.generate(GENERATOR_INPUT)
        self.assertNotIn("is_fallback", repr(b.requests[0].constraint_schema))


class Relax(unittest.TestCase):
    def test_relaxed_constraint_misses_rule_but_post_validation_catches(self):
        import json, jsonschema
        b, _, s = make({"LORA_A": [BAD_PARSE, GOOD_PARSE]})
        relaxed, removed = mr.relax(s._schemas.schema("ParserOutput"), b.supported_features)
        for kw in ("'allOf'", "'if'", "'then'", "'oneOf'", "'not'"):
            self.assertNotIn(kw, repr(relaxed))
        self.assertIn("allOf", removed)
        self.assertTrue(jsonschema.Draft202012Validator(relaxed).is_valid(json.loads(BAD_PARSE)))
        self.assertTrue(s._schemas.output_violations("LORA_A", json.loads(BAD_PARSE)))
        s.parse(PARSER_INPUT)
        self.assertEqual(s.last_call_info.attempts, 2)
        self.assertIn("allOf", s.last_call_info.relaxed)

    def test_never_narrows(self):
        import json, jsonschema
        _, _, s = make()
        for feats in ([], ["type", "properties", "enum", "anyOf", "$defs", "$ref"]):
            for role, good in (("ParserOutput", GOOD_PARSE), ("GeneratorOutput", '{"response_text":"a","is_fallback":false}')):
                orig = s._schemas.schema(role)
                v = jsonschema.Draft202012Validator(mr.relax(orig, feats)[0])
                for inst in (json.loads(good), {"items": []}, {"x": 1}, []):
                    if jsonschema.Draft202012Validator(orig).is_valid(inst):
                        self.assertTrue(v.is_valid(inst))

    def test_oneof_const_becomes_enum(self):
        r, removed = mr.relax({"oneOf": [{"const": "A"}, {"const": "B"}]}, ["enum"])
        self.assertEqual(r, {"enum": ["A", "B"]}); self.assertEqual(removed, ["oneOf->enum"])
        self.assertEqual(mr.relax({"oneOf": [{"type": "string"}]}, ["anyOf", "type"])[0],
                         {"anyOf": [{"type": "string"}]})


class Prompt(unittest.TestCase):
    def test_deterministic_and_version(self):
        cfg = mr.fake_serving_config()
        kw = dict(serving_config=cfg, schema_dir=SCHEMA_DIR)
        a = mr.render_prompt("LORA_A", PARSER_INPUT, **kw)
        b = mr.render_prompt("LORA_A", dict(reversed(list(PARSER_INPUT.items()))), **kw)
        self.assertEqual(a, b)
        self.assertIn("머리가 아파요", a)
        self.assertIn("UNRECOGNIZED", a)
        with self.assertRaises(mr.UnsupportedServingConfig):
            mr.render_prompt("LORA_A", PARSER_INPUT, serving_config=dict(cfg, prompt_template_version="x"), schema_dir=SCHEMA_DIR)
        with self.assertRaises(mr.ContractViolation):
            mr.render_prompt("LORA_B", PARSER_INPUT, **kw)


class Privacy(unittest.TestCase):
    def test_no_utterance_in_exceptions_or_logs(self):
        secret = {**PARSER_INPUT, "utterance": "비밀발화123"}
        _, _, s = make({"LORA_A": ['{"items":[{"source_text":"비밀발화123"}]}']}, max_attempts=1)
        with self.assertLogs("model_runtime", level=logging.DEBUG) as logs:
            logging.getLogger("model_runtime").debug("start")
            r = s.parse(secret)
            raw = s.parse_raw(secret)
        self.assertEqual(r, {"items": []})
        self.assertNotIn("비밀발화123", "\n".join(logs.output) + repr(s.last_call_info) + repr(raw.errors))
        with self.assertRaises(mr.ContractViolation) as cm:
            s.parse({**secret, "extra": "비밀발화123"})
        self.assertNotIn("비밀발화123", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
