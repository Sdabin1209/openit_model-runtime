"""prompt-4(D77): 학습 데이터와 글자 단위로 같은 직렬화. 학습 서버 데이터 대신 손으로 만든 fixture를 쓴다.
서버 val 대조는 scripts/check_prompt_matches_dataset.py(docs/runbook.md)."""
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from helpers import A_INPUT, B_INPUT, SCHEMA_DIR

import model_runtime as mr
from model_runtime import prompts

ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "dataset_samples.jsonl"
GOLD = Path(__file__).parent / "prompt_golden"
SYSTEM = "골든 시스템 프롬프트(시험용 문장)"


def cfg(version):
    return {"prompt_template_version": version}


def render(role, obj, version="prompt-4", system=SYSTEM):
    return mr.render_prompt(role, obj, serving_config=cfg(version), system_prompt=system, schema_dir=SCHEMA_DIR)


def user_part(text):
    return text.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0]


class Prompt4(unittest.TestCase):
    def test_default_is_prompt4_and_prompt3_deprecated(self):
        self.assertEqual(prompts.DEFAULT_TEMPLATE_VERSION, "prompt-4")
        self.assertEqual(mr.fake_serving_config()["prompt_template_version"], "prompt-4")
        self.assertEqual(prompts.DEPRECATED_TEMPLATE_VERSIONS, frozenset({"prompt-3"}))
        self.assertEqual(prompts.SUPPORTED_TEMPLATE_VERSIONS, frozenset({"prompt-3", "prompt-4"}))

    def test_golden(self):
        self.assertEqual(render("LORA_A", A_INPUT), (GOLD / "lora_a.prompt-4.txt").read_text(encoding="utf-8"))
        self.assertEqual(render("LORA_B", B_INPUT), (GOLD / "lora_b.prompt-4.txt").read_text(encoding="utf-8"))

    def test_prompt3_unchanged(self):
        self.assertEqual(render("LORA_A", A_INPUT, "prompt-3"), (GOLD / "lora_a.prompt-3.txt").read_text(encoding="utf-8"))
        self.assertEqual(render("LORA_B", B_INPUT, "prompt-3"), (GOLD / "lora_b.prompt-3.txt").read_text(encoding="utf-8"))

    def test_a_top_level_sorted_state_order_preserved(self):
        obj = {"user_utterance": "u", "state": {"Q9.x": {"value": 1, "status": "ANSWERED"}, "Q1.a": {"value": 2, "status": "ANSWERED"}},
               "question_id": "Q1.D01", "current_question": "q"}
        u = user_part(render("LORA_A", obj))
        self.assertEqual(u, '{"current_question": "q", "question_id": "Q1.D01", "state": {"Q9.x": {"value": 1, "status": "ANSWERED"}, '
                            '"Q1.a": {"value": 2, "status": "ANSWERED"}}, "user_utterance": "u"}')

    def test_b_all_levels_sorted_and_default_separators(self):
        obj = {"topic": "t", "next": {"type": "SAME", "text": "다음", "question_id": "Q1"}, "action": "REASK", "reason": "MISSING",
               "ask": "a", "known": "k", "user_utterance": "u"}
        u = user_part(render("LORA_B", obj))
        self.assertEqual(u, '{"action": "REASK", "ask": "a", "known": "k", "next": {"question_id": "Q1", "text": "다음", "type": "SAME"}, '
                            '"reason": "MISSING", "topic": "t", "user_utterance": "u"}')

    def test_non_ascii_not_escaped_and_same_as_json_dumps(self):
        u = user_part(render("LORA_A", A_INPUT))
        self.assertNotIn("\\u", u)
        self.assertEqual(u, json.dumps(dict(sorted(A_INPUT.items())), ensure_ascii=False))

    def test_input_not_mutated(self):
        import copy
        before = copy.deepcopy(B_INPUT)
        render("LORA_B", B_INPUT)
        self.assertEqual(B_INPUT, before)
        self.assertEqual(list(B_INPUT), list(before))

    def test_chat_structure_matches_qwen3_nothink(self):
        t = render("LORA_B", B_INPUT)
        self.assertTrue(t.startswith("<|im_start|>system\n" + SYSTEM + "<|im_end|>\n<|im_start|>user\n"))
        self.assertTrue(t.endswith("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"))

    def test_unknown_version_rejected(self):
        with self.assertRaises(mr.UnsupportedServingConfig):
            render("LORA_A", A_INPUT, "prompt-5")


class DatasetFixture(unittest.TestCase):
    """손으로 만든 fixture(학습 데이터 형식의 합성 2줄)가 스크립트로 일치해야 하고, 일부러 어긋낸 줄은 잡혀야 한다."""

    def run_script(self, text, *args):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import check_prompt_matches_dataset as c
        finally:
            sys.path.pop(0)
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(text)), redirect_stdout(out):
            rc = c.main(list(args))
        return rc, out.getvalue()

    def test_fixture_matches(self):
        rc, out = self.run_script(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0, out)
        self.assertIn("LORA_A: 행 1, user 글자 일치 1, 전체 프롬프트 일치 1", out)
        self.assertIn("LORA_B: 행 1, user 글자 일치 1, 전체 프롬프트 일치 1", out)

    def test_fixture_matches_with_shuffled_keys(self):
        rc, out = self.run_script(FIXTURE.read_text(encoding="utf-8"), "--shuffle")
        self.assertEqual(rc, 0, out)

    def test_detects_jcs_text(self):
        rows = [json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines()]
        u = json.loads(rows[1]["messages"][1]["content"])
        rows[1]["messages"][1]["content"] = json.dumps(u, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        rc, out = self.run_script("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        self.assertEqual(rc, 1)
        self.assertIn("어긋남: 행 2 LORA_B user", out)

    def test_empty_input(self):
        self.assertEqual(self.run_script("")[0], 2)


class Expectation(unittest.TestCase):
    def test_info_expectation_has_default_and_deprecated(self):
        from test_server import call, start
        from tempfile import TemporaryDirectory
        t = TemporaryDirectory(); self.addCleanup(t.cleanup)
        srv, url, _, _ = start(t.name); self.addCleanup(srv.shutdown)
        e = call(url, "/v1/info")[1]["serving_config_expectation"]
        self.assertEqual(e["prompt_template_versions"], ["prompt-3", "prompt-4"])
        self.assertEqual(e["prompt_template_version_default"], "prompt-4")
        self.assertEqual(e["deprecated_prompt_template_versions"], ["prompt-3"])


if __name__ == "__main__":
    unittest.main()
