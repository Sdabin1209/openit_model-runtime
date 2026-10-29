"""영역 안에 다시 정의한 스키마 대신 계약 스키마로 검증한다(U44, 계약 v2.5.0): ServingConfig, RuntimeProfile, RuntimeFlowFile.
검증기의 판정은 계약 패키지가 정한다. 이 영역은 손으로 쓴 스키마·키 목록·모양 검사를 두지 않는다."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import model_runtime as mr
from model_runtime import backends, serving_config, vllm_backend
from model_runtime.errors import ContractViolation

ROOT = Path(__file__).resolve().parents[1]


class NoOwnSchemas(unittest.TestCase):
    def test_no_hand_written_copies_remain(self):
        self.assertFalse(hasattr(serving_config, "SERVING_CONFIG_SCHEMA"))
        self.assertFalse(hasattr(vllm_backend, "_ENGINE_KEYS"))
        self.assertFalse(hasattr(backends, "_REASK_REASONS"))
        self.assertFalse(hasattr(backends, "_KEY"))


class ProfileFollowsContract(unittest.TestCase):
    def write(self, t, data):
        p = Path(t) / "p.json"; p.write_text(json.dumps(data), encoding="utf-8"); return p

    def test_shipped_profiles_pass(self):
        for f in sorted((ROOT / "profiles").glob("*.json")):
            mr.load_profile(f)

    def test_unknown_top_level_key_is_rejected(self):   # 계약 RuntimeProfile은 오타가 조용히 무시되지 않게 최상위도 닫는다(옛 코드는 무시했다)
        with TemporaryDirectory() as t, self.assertRaises(ContractViolation):
            mr.load_profile(self.write(t, {"engine": {"dtype": "bfloat16"}, "base_modle": "x"}))

    def test_unknown_engine_key_and_bad_type_are_rejected(self):
        with TemporaryDirectory() as t:
            for bad in ({"engine": {"nope": 1}}, {"engine": {"max_model_len": "4096"}}, {"engine": {"gpu_memory_utilization": 1.5}}):
                with self.assertRaises(ContractViolation, msg=bad):
                    mr.load_profile(self.write(t, bad))

    def test_comment_keys_are_allowed(self):
        with TemporaryDirectory() as t:
            self.assertEqual(mr.load_profile(self.write(t, {"_comment": "x", "engine": {"dtype": "bfloat16"}}))["engine"]["dtype"], "bfloat16")


class FlowFileFollowsContract(unittest.TestCase):
    def load(self, data):
        with TemporaryDirectory() as t:
            p = Path(t) / "f.json"; p.write_text(json.dumps(data), encoding="utf-8")
            return backends.load_fake_flow(str(p)), backends.load_fake_flow_spec(str(p))

    def test_both_formats_pass(self):
        self.assertEqual(self.load(["Q1.D01", "Q1.D02"])[0], ["Q1.D01", "Q1.D02"])
        self.assertEqual(self.load({"order": ["Q1.D01"]})[0], ["Q1.D01"])

    def test_typo_key_is_rejected(self):   # 옛 코드는 모르는 키를 무시해 `skip` 같은 오타가 사라졌다
        for bad in ({"order": ["Q1.D01"], "skip": []}, {"order": []}, ["Q1", "Q1"], {"order": ["Q1"], "slots": {"Q1": [{"key": "Q1.x", "type": "string"}]}}):
            with self.assertRaises(ValueError, msg=bad):
                self.load(bad)
