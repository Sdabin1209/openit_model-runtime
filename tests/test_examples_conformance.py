import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import BAD_PARSE, GENERATOR_INPUT, GOOD_GEN, GOOD_PARSE, PARSER_INPUT, SCHEMA_DIR

import model_runtime as mr

mr.configure(SCHEMA_DIR)
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
        """prompt-1의 출력이 바뀌면 이 테스트가 깨진다. 템플릿을 고치려면 새 버전(prompt-2)을 만든다."""
        cfg = ex("serving_config.fake.json")
        self.assertEqual(mr.render_prompt("LORA_A", ex("parser_input.json"), serving_config=cfg),
                         (EX / "parser_prompt.prompt-1.txt").read_text(encoding="utf-8"))
        self.assertEqual(mr.render_prompt("LORA_B", ex("generator_input.json"), serving_config=cfg),
                         (EX / "generator_prompt.prompt-1.txt").read_text(encoding="utf-8"))

    def test_example_end_to_end(self):
        script = {"LORA_A": [json.dumps(ex("parser_output.json"))], "LORA_B": [json.dumps(ex("generator_output.json"), ensure_ascii=False)]}
        b = mr.FakeBackend.scripted(script)
        cfg = ex("serving_config.fake.json")
        s = mr.FakeModelSession(cfg, expected_serving_config_hash=mr.serving_config_hash(cfg), backend=b)
        self.assertEqual(s.parse(ex("parser_input.json")), ex("parser_output.json"))
        self.assertEqual(s.generate(ex("generator_input.json")), ex("generator_output.json"))


# --- 적합성 표: load()로 만든 세션과 FakeModelSession이 같은 시나리오를 같은 결과로 통과해야 한다 ---
def _real(cfg, b, h):
    with TemporaryDirectory() as d:
        return mr.load({"LORA_A": Path(d), "LORA_B": Path(d)}, cfg, expected_serving_config_hash=h, backend=b)


def _fake(cfg, b, h):
    return mr.FakeModelSession(cfg, expected_serving_config_hash=h, backend=b)


FACTORIES = {"load": _real, "fake": _fake}
PARSE, GEN = "parse", "generate"
# (이름, 역할 스크립트, 호출, 입력, max_attempts, 기대: 시도 횟수 또는 예외)
SCENARIOS = [
    ("첫 시도 성공(파서)", {"LORA_A": [GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, 1),
    ("첫 시도 성공(생성기)", {"LORA_B": [GOOD_GEN]}, GEN, GENERATOR_INPUT, 3, 1),
    ("JSON 아님 뒤 성공", {"LORA_A": ["아님", GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, 2),
    ("스키마 위반 뒤 성공", {"LORA_B": ['{"response_text":""}', GOOD_GEN]}, GEN, GENERATOR_INPUT, 3, 2),
    ("완화로 놓친 규칙 위반 뒤 성공", {"LORA_A": [BAD_PARSE, GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, 2),
    ("마지막 시도에서 성공", {"LORA_A": ["x", "y", GOOD_PARSE]}, PARSE, PARSER_INPUT, 3, 3),
    ("상한 초과", {"LORA_A": [BAD_PARSE]}, PARSE, PARSER_INPUT, 2, mr.ModelOutputInvalid),
    ("max_attempts 1", {"LORA_B": ["{}"]}, GEN, GENERATOR_INPUT, 1, mr.ModelOutputInvalid),
    ("NaN 거부", {"LORA_B": ['{"response_text":"a","is_fallback":NaN}']}, GEN, GENERATOR_INPUT, 1, mr.ModelOutputInvalid),
    ("입력 계약 위반", {"LORA_A": [GOOD_PARSE]}, PARSE, {"utterance": "x"}, 3, mr.ContractViolation),
]


class Conformance(unittest.TestCase):
    def test_table(self):
        for fname, factory in FACTORIES.items():
            for name, script, call, inp, n, expect in SCENARIOS:
                with self.subTest(factory=fname, scenario=name):
                    b = mr.FakeBackend.scripted(script)
                    cfg = mr.fake_serving_config(b, max_attempts=n)
                    s = factory(cfg, b, mr.serving_config_hash(cfg))
                    if isinstance(expect, int):
                        getattr(s, call)(inp)
                        self.assertEqual(s.last_call_info.attempts, expect)
                        self.assertEqual([r.seed for r in b.requests], [1000 + i for i in range(expect)])
                    else:
                        with self.assertRaises(expect):
                            getattr(s, call)(inp)
                        if expect is mr.ContractViolation:
                            self.assertEqual(b.requests, [])

    def test_load_rejections_same(self):
        for fname, factory in FACTORIES.items():
            b = mr.FakeBackend(); cfg = mr.fake_serving_config(b)
            with self.subTest(factory=fname):
                with self.assertRaises(mr.ServingConfigMismatch):
                    factory(cfg, b, "sha256:" + "0" * 64)
                bad = dict(cfg, prompt_template_version="prompt-9")
                with self.assertRaises(mr.UnsupportedServingConfig):
                    factory(bad, b, mr.serving_config_hash(bad))


if __name__ == "__main__":
    unittest.main()
