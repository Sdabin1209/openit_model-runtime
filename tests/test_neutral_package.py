import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.canonical import content_hash, directory_hash, sha256_hash
from boundary_contracts.clients._http import check_record
from helpers import SCHEMA_DIR
from test_vllm_backend import adapters as _unused, fake_importer  # noqa: F401

import model_runtime as mr

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_neutral_package", ROOT / "scripts" / "make_neutral_package.py")
mk = importlib.util.module_from_spec(spec); spec.loader.exec_module(mk)


def build(out, **kw):
    a = dict(profile_path=ROOT / "profiles" / "gpu-12g.json", vllm_version="9.9.9", rank=8, revision="main",
             targets=["q_proj", "v_proj"], seed=0, model_config=None, max_tokens=512)
    a.update(kw)
    return mk.build(Path(out), **a)


class Profiles(unittest.TestCase):
    def test_all_profiles_load_and_have_required_shape(self):
        paths = sorted((ROOT / "profiles").glob("*.json"))
        self.assertGreaterEqual(len(paths), 3)
        for p in paths:
            prof = mr.load_profile(p)
            self.assertIsInstance(prof["base_model"], str, p.name)
            e = prof["engine"]
            for k in ("dtype", "max_model_len", "gpu_memory_utilization", "max_num_seqs", "max_loras", "max_lora_rank"):
                self.assertIn(k, e, p.name)
            self.assertTrue(0 < e["gpu_memory_utilization"] < 1, p.name)
            self.assertGreaterEqual(e["max_loras"], 2, p.name)   # 두 역할
            self.assertTrue(all(isinstance(v, str) for k, v in prof.get("env", {}).items() if not k.startswith("_")), p.name)

    def test_12g_profile_same_format_as_4090(self):
        a, b = mr.load_profile(ROOT / "profiles" / "gpu-12g.json"), mr.load_profile(ROOT / "profiles" / "rtx4090-24g.json")
        self.assertEqual((set(a), set(a["engine"])), (set(b), set(b["engine"])))
        self.assertEqual(a["base_model"], "Qwen/Qwen2.5-1.5B-Instruct")


class Neutral(unittest.TestCase):
    def test_manifest_schema_and_hash_rules(self):
        with TemporaryDirectory() as t:
            build(t)
            for role in ("LORA_A", "LORA_B"):
                d = Path(t) / role
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                check_record("ModelPackageManifest", m, hashed=True)   # 스키마 + content_hash
                self.assertEqual(m["adapter_hash"], directory_hash(d / "adapter"))
                self.assertEqual(m["serving_config_hash"], sha256_hash(m["serving_config"]))
                self.assertEqual(m["content_hash"], content_hash(m))
                self.assertEqual(m["role"], role)
                self.assertEqual(m["base_model"]["id"], "Qwen/Qwen2.5-1.5B-Instruct")
                self.assertTrue(m["ext"]["model-runtime"]["neutral"])
                self.assertIn("학습하지 않은", m["ext"]["model-runtime"]["note"])
                mr.validate_serving_config(m["serving_config"])
                self.assertEqual(m["serving_config"]["constraint"]["backend"], "vllm")

    def test_deterministic(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            sa, sb = build(a), build(b)
            self.assertEqual(sa, sb)
            for role in ("LORA_A", "LORA_B"):
                for f in ("manifest.json", "adapter/adapter_model.safetensors", "adapter/adapter_config.json"):
                    self.assertEqual((Path(a) / role / f).read_bytes(), (Path(b) / role / f).read_bytes())
            self.assertNotEqual(sa["LORA_A"]["adapter_hash"], build(Path(a) / "other", seed=5)["LORA_A"]["adapter_hash"])

    def test_adapter_is_peft_with_zero_b_and_passes_backend_precheck(self):
        with TemporaryDirectory() as t:
            build(t)
            ad = {r: Path(t) / r / "adapter" for r in ("LORA_A", "LORA_B")}
            cfg = json.loads((ad["LORA_A"] / "adapter_config.json").read_text())
            self.assertEqual((cfg["peft_type"], cfg["r"], cfg["base_model_name_or_path"]), ("LORA", 8, "Qwen/Qwen2.5-1.5B-Instruct"))
            raw = (ad["LORA_A"] / "adapter_model.safetensors").read_bytes()
            n = int.from_bytes(raw[:8], "little"); hdr = json.loads(raw[8:8 + n])
            body = raw[8 + n:]
            for name, t_ in hdr.items():
                if name.endswith("lora_B.weight"):
                    lo, hi = t_["data_offsets"]; self.assertEqual(set(body[lo:hi]), {0})   # B = 0
            # 프로파일의 기반 모델·순위 상한과 맞고, 서빙 설정이 load의 해시·백엔드 검증을 통과한다
            llms = []
            b = mr.VLLMBackend(None, profile=mr.load_profile(ROOT / "profiles" / "gpu-12g.json"), _import=fake_importer(llm_holder=llms)).bind(ad)
            cfgs, hs = {}, {}
            for r in ad:
                m = json.loads((Path(t) / r / "manifest.json").read_text())
                cfgs[r], hs[r] = m["serving_config"], m["serving_config_hash"]
            mr.load(ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)

    def test_rank_above_profile_max_and_unknown_model_rejected(self):
        with TemporaryDirectory() as t:
            with self.assertRaises(SystemExit):
                build(t, rank=32)
            p = Path(t) / "p.json"; p.write_text(json.dumps({"base_model": "x/unknown", "engine": {}}))
            with self.assertRaises(SystemExit):
                build(Path(t) / "o", profile_path=p)
