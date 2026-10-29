import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.canonical import content_hash, directory_hash, sha256_hash
from boundary_contracts.clients._http import check_record
from boundary_contracts.canonical import file_hash
from helpers import SCHEMA_DIR
from test_vllm_backend import adapters as _unused, fake_importer  # noqa: F401

import model_runtime as mr

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_neutral_package", ROOT / "scripts" / "make_neutral_package.py")
mk = importlib.util.module_from_spec(spec); spec.loader.exec_module(mk)


def build(out, **kw):
    a = dict(profile_path=ROOT / "profiles" / "gpu-dev-12g.json", vllm_version="9.9.9", rank=8, revision="main",
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
            self.assertGreaterEqual(e["max_lora_rank"], 16, p.name)   # 학습 어댑터 rank 16(D74)
            self.assertTrue(all(isinstance(v, str) for k, v in prof.get("env", {}).items() if not k.startswith("_")), p.name)
            self.assertIn("loadable", prof["status"], p.name)

    def test_12g_profile_same_format_as_4090(self):
        a, b = mr.load_profile(ROOT / "profiles" / "gpu-12g.json"), mr.load_profile(ROOT / "profiles" / "rtx4090-24g.json")
        self.assertEqual((set(a), set(a["engine"])), (set(b), set(b["engine"])))

    def test_qwen3_profiles_per_model_and_loadability_is_unverified(self):
        """D74·D81: 기반 모델은 프로파일로 고른다. 12GB급은 Qwen3-1.7B(파이프라인 데모용), 24GB급은 Qwen3-8B. 적재 가능 여부와 A·B 동시 적재는 측정 전이라 미확인이다."""
        a, b = mr.load_profile(ROOT / "profiles" / "gpu-12g.json"), mr.load_profile(ROOT / "profiles" / "rtx4090-24g.json")
        self.assertEqual((a["base_model"], b["base_model"]), ("Qwen/Qwen3-1.7B", "Qwen/Qwen3-8B"))
        # gpu-12g는 2026-10-27 gpu-dev에서 적재를 측정했다(U01, CHANGELOG 참조). A·B 동시 적재는 간접 확인뿐이라 unverified로 둔다
        self.assertEqual((a["status"]["loadable"], a["status"]["two_adapters_loaded_together"]), ("measured", "unverified"))
        self.assertEqual((b["status"]["loadable"], b["status"]["two_adapters_loaded_together"]), ("unverified", "unverified"))
        for p in (a, b):
            self.assertEqual(p["engine"]["max_loras"], 2)
        self.assertEqual(a["status"]["for"], "pipeline-demo")
        # 측정한 프로파일(Qwen2.5-1.5B)은 파이프라인 시험 전용으로 표시된다
        dev = mr.load_profile(ROOT / "profiles" / "gpu-dev-12g.json")
        self.assertEqual((dev["base_model"], dev["status"]["for"]), ("Qwen/Qwen2.5-1.5B-Instruct", "pipeline-check-only"))

    def test_adapter_base_model_must_match_profile_base_model(self):
        """어댑터의 기반 모델과 프로파일의 기반 모델이 다르면 적재를 거부한다(서버는 412로 낸다)."""
        from test_vllm_backend import adapters
        with TemporaryDirectory() as t:
            llms = []
            prof = mr.load_profile(ROOT / "profiles" / "gpu-12g.json")
            b = mr.VLLMBackend(None, profile=prof, _import=fake_importer(llm_holder=llms))
            self.assertEqual(llms[0].init_kw["model"], "Qwen/Qwen3-1.7B")
            Path(t, "ok").mkdir(); Path(t, "bad").mkdir(); Path(t, "r32").mkdir()
            b.bind(adapters(Path(t) / "ok", base="Qwen/Qwen3-1.7B", rank=16))
            with self.assertRaises(mr.UnsupportedServingConfig) as cm:
                b.bind(adapters(Path(t) / "bad", base="Qwen/Qwen3-8B", rank=16))   # 8B 어댑터를 1.7B 프로파일에
            from model_runtime import server
            self.assertEqual(server.to_boundary(cm.exception).code, "FAILED_PRECONDITION")
            with self.assertRaises(mr.UnsupportedServingConfig):
                b.bind(adapters(Path(t) / "r32", base="Qwen/Qwen3-1.7B", rank=32))


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
                # 시스템 프롬프트는 패키지의 일부(D75): 어댑터 디렉터리 안의 파일이고 해시가 맞다(adapter_hash에도 포함된다)
                self.assertEqual(m["system_prompt"]["path"], "system_prompt.txt")
                self.assertEqual(m["system_prompt"]["sha256"], file_hash(d / "adapter" / "system_prompt.txt"))
                self.assertEqual(m["serving_config"]["decoding"]["constraint_mode"], "constrained" if role == "LORA_A" else "none")
                self.assertTrue(m["ext"]["model-runtime"]["neutral"])
                self.assertIn("학습하지 않은", m["ext"]["model-runtime"]["note"])
                mr.validate_serving_config(m["serving_config"])
                self.assertEqual(m["serving_config"]["constraint"]["backend"], "vllm")

    def test_system_prompt_args_set_path_and_hash(self):
        """--system-prompt-a/-b: 파일 내용이 그대로 패키지에 들어가고 manifest.system_prompt의 해시가 그 파일의 것이다. 같은 인자면 같은 바이트."""
        import hashlib
        import subprocess
        import sys
        prompts = {"LORA_A": "프롬프트 A\n여러 줄\n".encode(), "LORA_B": "프롬프트 B".encode()}
        with TemporaryDirectory() as t, TemporaryDirectory() as t2:
            fa, fb = Path(t) / "a.txt", Path(t) / "b.txt"
            fa.write_bytes(prompts["LORA_A"]); fb.write_bytes(prompts["LORA_B"])
            out = Path(t) / "out"
            args = [sys.executable, str(ROOT / "scripts" / "make_neutral_package.py"), str(out), "--profile",
                    str(ROOT / "profiles" / "gpu-dev-12g.json"), "--system-prompt-a", str(fa), "--system-prompt-b", str(fb)]
            r = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            for role, data in prompts.items():
                d = out / role
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                check_record("ModelPackageManifest", m, hashed=True)
                self.assertEqual((d / "adapter" / "system_prompt.txt").read_bytes(), data)
                self.assertEqual(m["system_prompt"], {"path": "system_prompt.txt", "sha256": "sha256:" + hashlib.sha256(data).hexdigest()})
                self.assertEqual(m["adapter_hash"], directory_hash(d / "adapter"))   # 프롬프트가 어댑터 해시에 들어간다
            r2 = subprocess.run([*args[:2], str(Path(t2) / "o2"), *args[3:]], capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual((out / "SUMMARY.json").read_bytes(), (Path(t2) / "o2" / "SUMMARY.json").read_bytes())
            # 한쪽만 주면 다른 쪽은 자리표시 문장
            sa = build(Path(t) / "o3", system_prompts={"LORA_A": prompts["LORA_A"]})
            self.assertNotEqual(sa["LORA_A"]["adapter_hash"], build(Path(t) / "o4")["LORA_A"]["adapter_hash"])
            self.assertEqual(sa["LORA_B"]["adapter_hash"], build(Path(t) / "o4")["LORA_B"]["adapter_hash"])
            # 빈 파일은 거부
            fe = Path(t) / "e.txt"; fe.write_text(" \n")
            r3 = subprocess.run([*args[:2], str(Path(t) / "o5"), *args[3:5], "--system-prompt-a", str(fe)], capture_output=True, text=True)
            self.assertNotEqual(r3.returncode, 0)

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
            b = mr.VLLMBackend(None, profile=mr.load_profile(ROOT / "profiles" / "gpu-dev-12g.json"), _import=fake_importer(llm_holder=llms)).bind(ad)
            cfgs, hs = {}, {}
            refs = {}
            for r in ad:
                m = json.loads((Path(t) / r / "manifest.json").read_text())
                cfgs[r], hs[r], refs[r] = m["serving_config"], m["serving_config_hash"], m["system_prompt"]
            s = mr.load(ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs, backend=b)
            self.assertEqual(s.system_prompt_hashes, {r: refs[r]["sha256"] for r in refs})

    def test_rank_above_profile_max_and_unknown_model_rejected(self):
        with TemporaryDirectory() as t:
            with self.assertRaises(SystemExit):
                build(t, rank=32)
            p = Path(t) / "p.json"; p.write_text(json.dumps({"base_model": "x/unknown", "engine": {}}))
            with self.assertRaises(SystemExit):
                build(Path(t) / "o", profile_path=p)


class NeutralQwen3(unittest.TestCase):
    """Qwen3처럼 head_dim이 은닉 차원/헤드 수와 다른 모델. 아래 차원은 시험용 임의 값이며 Qwen3의 실제 값이 아니다."""
    CFG = {"hidden_size": 64, "num_hidden_layers": 2, "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 32}

    def test_requires_model_config_for_qwen3_and_uses_head_dim(self):
        with TemporaryDirectory() as t:
            with self.assertRaises(SystemExit):   # 확인하지 못한 수치를 내장하지 않는다
                build(Path(t) / "x", profile_path=ROOT / "profiles" / "gpu-12g.json")
            cfg = Path(t) / "config.json"; cfg.write_text(json.dumps(self.CFG))
            build(Path(t) / "o", profile_path=ROOT / "profiles" / "gpu-12g.json", model_config=str(cfg), rank=4, targets=["q_proj", "o_proj"])
            raw = (Path(t) / "o" / "LORA_A" / "adapter" / "adapter_model.safetensors").read_bytes()
            n = int.from_bytes(raw[:8], "little"); hdr = json.loads(raw[8:8 + n])
            p = "base_model.model.model.layers.0.self_attn."
            self.assertEqual(hdr[p + "q_proj.lora_A.weight"]["shape"], [4, 64])
            self.assertEqual(hdr[p + "q_proj.lora_B.weight"]["shape"], [4 * 32, 4])   # heads x head_dim = 128, 은닉 차원(64)이 아니다
            self.assertEqual(hdr[p + "o_proj.lora_A.weight"]["shape"], [4, 128])
            self.assertEqual(hdr[p + "o_proj.lora_B.weight"]["shape"], [64, 4])
            m = json.loads((Path(t) / "o" / "LORA_A" / "manifest.json").read_text())
            self.assertEqual(m["base_model"]["id"], "Qwen/Qwen3-1.7B")


class ActionRulesVersion(unittest.TestCase):
    """매니페스트의 계보 표지 action_rules_version(계약 3.0.0)은 하드코딩이 아니라 계약 규칙 데이터의 rules_version에서 온다."""
    def test_neutral_package_lineage_uses_the_contract_rules_version(self):
        from boundary_contracts.action_rules import load_rules
        with TemporaryDirectory() as t:
            build(Path(t) / "a")
            m = json.loads((Path(t) / "a" / "LORA_A" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(m["lineage"]["action_rules_version"], load_rules()["rules_version"])
            self.assertEqual(mr.ACTION_RULES_VERSION, load_rules()["rules_version"])
            self.assertNotIn("turn_engine_version", m["lineage"])
            build(Path(t) / "b", action_rules_version="9")
            m = json.loads((Path(t) / "b" / "LORA_A" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(m["lineage"]["action_rules_version"], "9")
