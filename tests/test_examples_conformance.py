import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import BAD_PARSE, GENERATOR_INPUT, GOOD_GEN, GOOD_PARSE, PARSER_INPUT, SCHEMA_DIR, cfgs_and_hashes

import model_runtime as mr

EX = Path(__file__).parent / "examples"


def ex(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


class Examples(unittest.TestCase):
    def test_examples_validate_against_schema(self):
        s = mr.SchemaSet(SCHEMA_DIR)
        s.validate_input("LORA_A", ex("parser_input.json"))
        s.validate_input("LORA_B", ex("generator_input.json"))
        self.assertEqual(s.output_violations("LORA_A", ex("parser_output.json")), [])
        self.assertEqual(s.output_violations("LORA_B", ex("generator_output.json")), [])

    def test_serving_config_hash_pinned(self):
        cfg = ex("serving_config.fake.json")
        self.assertEqual(mr.serving_config_hash(cfg), (EX / "serving_config.fake.hash").read_text().strip())

    def test_prompt_golden(self):
        """prompt-2의 출력이 바뀌면 이 테스트가 깨진다. 템플릿을 고치려면 새 버전(prompt-3)을 만든다."""
        cfg = ex("serving_config.fake.json")
        kw = dict(serving_config=cfg, schema_dir=SCHEMA_DIR)
        self.assertEqual(mr.render_prompt("LORA_A", ex("parser_input.json"), **kw),
                         (EX / "parser_prompt.prompt-2.txt").read_text(encoding="utf-8"))
        self.assertEqual(mr.render_prompt("LORA_B", ex("generator_input.json"), **kw),
                         (EX / "generator_prompt.prompt-2.txt").read_text(encoding="utf-8"))

    def test_example_end_to_end(self):
        script = {"LORA_A": [json.dumps(ex("parser_output.json"))], "LORA_B": [json.dumps({"response_text": ex("generator_output.json")["response_text"]}, ensure_ascii=False)]}
        b = mr.FakeBackend.scripted(script)
        cfg = ex("serving_config.fake.json")
        cfgs = {"LORA_A": cfg, "LORA_B": cfg}
        hs = {r: mr.serving_config_hash(c) for r, c in cfgs.items()}
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        self.assertEqual(s.parse(ex("parser_input.json")), ex("parser_output.json"))
        self.assertEqual(s.generate(ex("generator_input.json")), ex("generator_output.json"))


# --- 적합성 표: load()로 만든 세션과 FakeModelSession이 같은 시나리오를 같은 결과로 통과해야 한다 ---
def _real(cfgs, b, hs):
    with TemporaryDirectory() as d:
        return mr.load({"LORA_A": Path(d), "LORA_B": Path(d)}, cfgs, schema_dir=SCHEMA_DIR,
                       expected_serving_config_hashes=hs, backend=b)


def _fake(cfgs, b, hs):
    return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)


FACTORIES = {"load": _real, "fake": _fake}
PARSE, GEN = "parse", "generate"
# (이름, 역할 스크립트, 호출, 입력, max_attempts, 기대: 시도 횟수와 결과 종류 또는 예외)
# 결과 종류: ok | repaired | fallback. 모델 출력이 깨져도 예외 없이 규칙이 정한 값이 나온다(D36).
SCENARIOS = [
    ("첫 시도 성공(파서)", {"LORA_A": [GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, (1, "ok")),
    ("첫 시도 성공(생성기)", {"LORA_B": [GOOD_GEN]}, GEN, GENERATOR_INPUT, 3, (1, "ok")),
    ("JSON 아님 뒤 성공", {"LORA_A": ["아님", GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, (2, "ok")),
    ("스키마 위반 뒤 성공", {"LORA_B": ['{"response_text":""}', GOOD_GEN]}, GEN, GENERATOR_INPUT, 3, (2, "ok")),
    ("규칙 위반 뒤 성공", {"LORA_A": [BAD_PARSE, GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, (2, "ok")),
    ("마지막 시도에서 성공", {"LORA_A": ["x", "y", GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, (3, "ok")),
    ("상한 뒤 파서 항목 보호", {"LORA_A": [BAD_PARSE]}, PARSE, PARSER_INPUT, 2, (2, "repaired")),
    ("상한 뒤 파서 빈 목록", {"LORA_A": ["x"]}, PARSE, PARSER_INPUT, 2, (2, "fallback")),
    ("상한 뒤 생성기 대체 문장", {"LORA_B": ["{}"]}, GEN, GENERATOR_INPUT, 1, (1, "fallback")),
    ("NaN 거부", {"LORA_B": ['{"response_text":"a","is_fallback":NaN}']}, GEN, GENERATOR_INPUT, 1, (1, "fallback")),
    ("입력 계약 위반", {"LORA_A": [GOOD_PARSE]}, PARSE, {"utterance": "x"}, 3, mr.ContractViolation),
]


class Conformance(unittest.TestCase):
    def test_table(self):
        for fname, factory in FACTORIES.items():
            for name, script, call, inp, n, expect in SCENARIOS:
                with self.subTest(factory=fname, scenario=name):
                    b = mr.FakeBackend.scripted(script)
                    cfgs, hs = cfgs_and_hashes(b, {"max_attempts": n}, {"max_attempts": n})
                    s = factory(cfgs, b, hs)
                    if isinstance(expect, tuple):
                        out = getattr(s, call)(inp)
                        self.assertEqual((s.last_call_info.attempts, s.last_call_info.outcome), expect)
                        self.assertEqual([r.seed for r in b.requests], [1000 + i for i in range(expect[0])])
                        self.assertEqual(mr.SchemaSet(SCHEMA_DIR).output_violations("LORA_A" if call == PARSE else "LORA_B", out), [])
                    else:
                        with self.assertRaises(expect):
                            getattr(s, call)(inp)
                        if expect is mr.ContractViolation:
                            self.assertEqual(b.requests, [])

    def test_load_rejections_same(self):
        for fname, factory in FACTORIES.items():
            b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
            with self.subTest(factory=fname):
                for role in ("LORA_A", "LORA_B"):          # 역할 하나만 어긋난 해시
                    with self.assertRaises(mr.ServingConfigMismatch) as cm:
                        factory(cfgs, b, dict(hs, **{role: "sha256:" + "0" * 64}))
                    self.assertEqual(cm.exception.role, role)
                bad_a, bad_hs = cfgs_and_hashes(b, {"prompt_template_version": "prompt-9"})
                with self.assertRaises(mr.UnsupportedServingConfig):
                    factory(bad_a, b, bad_hs)
                with self.assertRaises(mr.ContractViolation):   # 역할 집합 불일치
                    factory({"LORA_A": cfgs["LORA_A"]}, b, hs)
                with self.assertRaises(mr.ContractViolation):
                    factory(cfgs, b, {"LORA_A": hs["LORA_A"]})


if __name__ == "__main__":
    unittest.main()
