import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import A_INPUT, B_INPUT, BAD_A, GOOD_A, GOOD_B, SCHEMA_DIR, assert_valid, cfgs_and_hashes
from test_runtime import prompts_in

import model_runtime as mr

GOLD = Path(__file__).parent / "prompt_golden"
SYSTEM = "골든 시스템 프롬프트(시험용 문장)"


class Golden(unittest.TestCase):
    def test_serving_config_hash_pinned(self):
        cfg = mr.fake_serving_config()
        self.assertEqual(mr.serving_config_hash(cfg), (GOLD / "serving_config.fake.hash").read_text().strip())
        self.assertEqual(json.loads((GOLD / "serving_config.fake.json").read_text(encoding="utf-8")), cfg)

    def test_prompt_golden(self):
        """prompt-4(기본)의 출력이 바뀌면 이 테스트가 깨진다. 템플릿을 고치려면 새 버전을 만든다. prompt-3(폐기 예정)은 test_prompt4.py가 고정한다.
        이 파일은 이 리포지토리가 만든 문자열의 고정일 뿐이며 Qwen3 토크나이저의 결과와 같은지는 확인하지 못했다(prompts.py 머리말)."""
        kw = dict(serving_config=json.loads((GOLD / "serving_config.fake.json").read_text(encoding="utf-8")),
                  system_prompt=SYSTEM, schema_dir=SCHEMA_DIR)
        self.assertEqual(mr.render_prompt("LORA_A", A_INPUT, **kw), (GOLD / "lora_a.prompt-4.txt").read_text(encoding="utf-8"))
        self.assertEqual(mr.render_prompt("LORA_B", B_INPUT, **kw), (GOLD / "lora_b.prompt-4.txt").read_text(encoding="utf-8"))

    def test_example_end_to_end(self):
        b = mr.FakeBackend.scripted({"LORA_A": [GOOD_A], "LORA_B": [GOOD_B]})
        cfgs, hs = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        out = s.extract(A_INPUT)
        assert_valid("ActionExtraction", out)
        self.assertEqual(s.respond(B_INPUT), mr.ResponseResult(GOOD_B, False))


# --- 적합성 표: load()로 만든 세션과 FakeModelSession이 같은 시나리오를 같은 결과로 통과해야 한다 ---
def _real(cfgs, b, hs):
    d = TemporaryDirectory()
    ad, refs = prompts_in(d.name)
    s = mr.load(ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs, backend=b)
    s._keep = d   # 임시 디렉터리를 세션과 함께 둔다
    return s


def _fake(cfgs, b, hs):
    return mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)


FACTORIES = {"load": _real, "fake": _fake}
EXTRACT, RESPOND = "extract", "respond"
# (이름, 역할 스크립트, 호출, 입력, max_attempts, 기대: (시도 횟수, 결과 종류) 또는 예외)
# 결과 종류: ok | fallback(respond만). extract가 끝내 실패하면 ModelOutputInvalid(D75), 대체 출력은 없다.
SCENARIOS = [
    ("첫 시도 성공(extract)", {"LORA_A": [GOOD_A]}, EXTRACT, A_INPUT, 3, (1, "ok")),
    ("첫 시도 성공(respond)", {"LORA_B": [GOOD_B]}, RESPOND, B_INPUT, 3, (1, "ok")),
    ("JSON 아님 뒤 성공", {"LORA_A": ["아님", GOOD_A]}, EXTRACT, A_INPUT, 3, (2, "ok")),
    ("스키마 위반 뒤 성공", {"LORA_A": [BAD_A, GOOD_A]}, EXTRACT, A_INPUT, 3, (2, "ok")),
    ("빈 문장 뒤 성공", {"LORA_B": ["", GOOD_B]}, RESPOND, B_INPUT, 3, (2, "ok")),
    ("마지막 시도에서 성공", {"LORA_A": ["x", "y", GOOD_A]}, EXTRACT, A_INPUT, 3, (3, "ok")),
    ("상한 뒤 extract는 실패", {"LORA_A": [BAD_A]}, EXTRACT, A_INPUT, 2, mr.ModelOutputInvalid),
    ("상한 뒤 respond는 대체 문장", {"LORA_B": [" "]}, RESPOND, B_INPUT, 1, (1, "fallback")),
    ("NaN 거부", {"LORA_A": ['{"values":[{"key":"Q10.days","value":NaN}],"action":"NEXT","next_item":"END"}']}, EXTRACT, A_INPUT, 1, mr.ModelOutputInvalid),
    ("입력 계약 위반", {"LORA_A": [GOOD_A]}, EXTRACT, {"user_utterance": "x"}, 3, mr.ContractViolation),
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
                        if call == EXTRACT:
                            self.assertEqual(mr.SchemaSet(SCHEMA_DIR).output_violations("LORA_A", out), [])
                        else:
                            self.assertEqual(out.is_fallback, expect[1] == "fallback")
                            self.assertEqual(mr.SchemaSet(SCHEMA_DIR).output_violations("LORA_B", out.text), [])
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
