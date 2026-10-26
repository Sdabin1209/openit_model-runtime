"""VLLMBackend 단위 시험. vLLM은 모킹한다(이 환경에는 없다). 실제 vLLM 동작은 scripts/gpu_check.sh로 확인한다."""
import json
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.canonical import file_hash
from helpers import A_INPUT, B_INPUT, GOOD_A, GOOD_B, SCHEMA_DIR

import model_runtime as mr

ROOT = Path(__file__).parent.parent


class FakeSP:
    class SamplingParams:
        def __init__(self, **kw): self.kw = kw

    class StructuredOutputsParams:
        def __init__(self, json): self.json = json


class OldSP:   # 구버전 이름(GuidedDecodingParams)
    SamplingParams = FakeSP.SamplingParams

    class GuidedDecodingParams:
        def __init__(self, json): self.json = json


class FakeLLM:
    def __init__(self, **kw):
        self.init_kw = kw
        self.calls = []
        self.texts = []

    def generate(self, prompts, sp, lora_request=None, use_tqdm=True):
        self.calls.append((prompts, sp, lora_request))
        text = self.texts.pop(0) if self.texts else GOOD_A
        return [types.SimpleNamespace(outputs=[types.SimpleNamespace(text=text, finish_reason="stop")])]


class LoRARequest:
    def __init__(self, name, int_id, path):
        self.name, self.int_id, self.path = name, int_id, path


def fake_importer(sp=FakeSP, llm_holder=None):
    llm_holder = llm_holder if llm_holder is not None else []

    def make_llm(**kw):
        llm_holder.append(FakeLLM(**kw))
        return llm_holder[-1]

    mods = {"vllm": types.SimpleNamespace(__version__="9.9.9", LLM=make_llm),
            "vllm.sampling_params": sp,
            "vllm.lora.request": types.SimpleNamespace(LoRARequest=LoRARequest)}

    def imp(name):
        if name not in mods:
            raise ImportError(name)
        return mods[name]

    return imp


def adapters(tmp, base="Qwen/Qwen2.5-1.5B-Instruct", rank=8):
    out = {}
    for role in ("LORA_A", "LORA_B"):
        d = Path(tmp) / role
        d.mkdir()
        (d / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": base, "r": rank}))
        (d / "system_prompt.txt").write_text(f"{role} 시스템 프롬프트", encoding="utf-8")   # 패키지의 프롬프트 파일(D75)
        out[role] = d
    return out


def refs(ad):
    return {r: {"path": "system_prompt.txt", "sha256": file_hash(Path(d) / "system_prompt.txt")} for r, d in ad.items()}


def request(role="LORA_A", schema=None, seed=1000, attempt=0, **dec):
    d = {"constraint_mode": "constrained", "temperature": 0, "seed": 1000, "max_tokens": 128}
    d.update(dec)
    return mr.GenerationRequest(role, "p", schema, d, seed, attempt)


class Assembly(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.ad = adapters(self._t.name)
        self.llms = []

    def backend(self, **kw):
        return mr.VLLMBackend(self.ad, _import=fake_importer(llm_holder=self.llms, sp=kw.pop("sp", FakeSP)), **kw)

    def test_not_installed_is_backend_not_available(self):
        with self.assertRaises(mr.BackendNotAvailable):
            mr.VLLMBackend(self.ad)   # 이 환경에는 vllm이 없다
        cfgs = mr.fake_serving_configs()
        for c in cfgs.values():
            c["constraint"]["backend"] = "vllm"
        hs = {r: mr.serving_config_hash(c) for r, c in cfgs.items()}
        with self.assertRaises(mr.BackendNotAvailable):
            mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad))

    def test_two_adapters_one_base_model_and_profile(self):
        prof = mr.load_profile(ROOT / "profiles" / "gpu-dev-12g.json")
        b = self.backend(profile=prof)
        kw = self.llms[0].init_kw
        self.assertEqual(kw["model"], "Qwen/Qwen2.5-1.5B-Instruct")
        self.assertTrue(kw["enable_lora"])
        self.assertEqual((kw["max_loras"], kw["max_lora_rank"], kw["gpu_memory_utilization"]), (2, 16, 0.6))
        self.assertEqual((b.name, b.version), ("vllm", "9.9.9"))

    def test_base_model_from_adapter_config(self):
        self.backend()
        self.assertEqual(self.llms[0].init_kw["model"], "Qwen/Qwen2.5-1.5B-Instruct")

    def test_base_model_mismatch_rejected_before_engine(self):
        (self.ad["LORA_B"] / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "other", "r": 8}))
        with self.assertRaises(mr.UnsupportedServingConfig):
            self.backend()
        with self.assertRaises(mr.UnsupportedServingConfig):   # 명시한 기반 모델과 달라도 거부
            self.backend(base_model="x/y")
        self.assertEqual(self.llms, [])

    def test_rank_above_max_rejected_before_engine(self):
        (self.ad["LORA_A"] / "adapter_config.json").write_text(
            json.dumps({"base_model_name_or_path": "Qwen/Qwen2.5-1.5B-Instruct", "r": 32}))
        with self.assertRaises(mr.UnsupportedServingConfig) as c:
            self.backend(profile=mr.load_profile(ROOT / "profiles" / "gpu-dev-12g.json"))
        self.assertIn("LORA_A", str(c.exception))
        self.assertEqual(self.llms, [])   # 엔진을 만들지 않았다
        self.backend(profile={"engine": {"max_lora_rank": 32}})   # 상한을 올리면 통과

    def test_non_peft_adapter_rejected(self):
        (self.ad["LORA_A"] / "adapter_config.json").write_text("{}")
        with self.assertRaises(mr.ContractViolation):
            self.backend()
        (self.ad["LORA_A"] / "adapter_config.json").unlink()
        with self.assertRaises(mr.ContractViolation):
            self.backend()
        self.assertEqual(self.llms, [])

    def test_adapter_roles_must_be_both(self):
        with self.assertRaises(mr.ContractViolation):
            mr.VLLMBackend({"LORA_A": self.ad["LORA_A"]}, _import=fake_importer())

    def test_profile_rejects_unknown_engine_key(self):
        with TemporaryDirectory() as t:
            p = Path(t) / "p.json"
            p.write_text(json.dumps({"engine": {"bogus": 1}}))
            with self.assertRaises(mr.ContractViolation):
                mr.load_profile(p)

    def test_lora_selected_by_role(self):
        b = self.backend()
        b.generate(request("LORA_A")); b.generate(request("LORA_B"))
        (_, _, la), (_, _, lb) = self.llms[0].calls
        self.assertEqual((la.name, lb.name), ("LORA_A", "LORA_B"))
        self.assertNotEqual(la.int_id, lb.int_id)
        self.assertEqual(Path(la.path), self.ad["LORA_A"])

    def test_sampling_params_seed_temperature_schema(self):
        b = self.backend()
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        sp = b.build_sampling_params(request(schema=schema, seed=1002, attempt=2, temperature=0.7, top_p=0.9))
        self.assertEqual((sp.kw["seed"], sp.kw["temperature"], sp.kw["top_p"], sp.kw["max_tokens"]), (1002, 0.7, 0.9, 128))
        self.assertEqual(json.loads(sp.kw["structured_outputs"].json), schema)
        self.assertNotIn("guided_decoding", sp.kw)

    def test_old_vllm_uses_guided_decoding_and_none_mode_has_no_constraint(self):
        b = self.backend(sp=OldSP)
        sp = b.build_sampling_params(request(schema={"type": "object"}))
        self.assertIn("guided_decoding", sp.kw)
        sp = b.build_sampling_params(request(schema=None, constraint_mode="none"))
        self.assertNotIn("guided_decoding", sp.kw); self.assertNotIn("structured_outputs", sp.kw)

    def test_generate_returns_text(self):
        b = self.backend(); self.llms[0].texts = ["abc"]
        self.assertEqual(b.generate(request()), "abc")


class Session(unittest.TestCase):
    """VLLMBackend(모킹)를 load에 꽂아 역할별 설정·시드·완화를 확인한다."""

    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.ad = adapters(self._t.name)
        self.llms = []
        self.b = mr.VLLMBackend(self.ad, _import=fake_importer(llm_holder=self.llms))

    def cfgs(self, a=None, b=None):
        cfgs = mr.fake_serving_configs(self.b, LORA_A=a, LORA_B=b)
        return cfgs, {r: mr.serving_config_hash(c) for r, c in cfgs.items()}

    def test_seed_is_base_plus_attempt_and_relaxed_schema_sent(self):
        cfgs, hs = self.cfgs(a={"decoding": {"constraint_mode": "constrained", "temperature": 0.5, "seed": 7, "max_tokens": 64}})
        s = mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)
        self.llms[0].texts = ["not json", "{}", GOOD_A]
        s.extract(A_INPUT)
        seeds = [c[1].kw["seed"] for c in self.llms[0].calls]
        self.assertEqual(seeds, [7, 8, 9])
        self.assertEqual({c[1].kw["temperature"] for c in self.llms[0].calls}, {0.5})
        sent = json.loads(self.llms[0].calls[0][1].kw["structured_outputs"].json)
        def keys(n):
            if isinstance(n, dict):
                for k, v in n.items():
                    yield k
                    yield from keys(v)
            elif isinstance(n, list):
                for v in n:
                    yield from keys(v)
        used = set(keys(sent))
        for kw in ("oneOf", "if", "then", "not", "allOf", "pattern", "minItems", "maxItems", "$comment"):
            self.assertNotIn(kw, used)   # 지원 집합 밖 키워드는 완화로 빠진다(설명 문장 안의 글자는 해당 없음)
        self.assertEqual(s.last_call_info.attempts, 3)
        self.assertIn("allOf", s.last_call_info.relaxed)
        self.assertIn("pattern", s.last_call_info.relaxed)   # 키 문법(pattern)은 엔진 지원이 미확인이라 보내지 않는다

    def test_roles_use_own_decoding_and_adapter(self):
        cfgs, hs = self.cfgs(b={"decoding": {"constraint_mode": "constrained", "temperature": 0.3, "seed": 50, "max_tokens": 32}})
        s = mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)
        self.llms[0].texts = [GOOD_A, GOOD_B]
        s.extract(A_INPUT); s.respond(B_INPUT)
        (_, spa, la), (_, spb, lb) = self.llms[0].calls
        self.assertEqual((la.name, spa.kw["seed"], spa.kw["temperature"]), ("LORA_A", 1000, 0))
        self.assertEqual((lb.name, spb.kw["seed"], spb.kw["temperature"], spb.kw["max_tokens"]), ("LORA_B", 50, 0.3, 32))

    def test_config_must_name_this_backend(self):
        cfgs, hs = self.cfgs()
        cfgs["LORA_A"]["constraint"]["backend_version"] = "1.0.0"
        hs = {r: mr.serving_config_hash(c) for r, c in cfgs.items()}
        with self.assertRaises(mr.UnsupportedServingConfig):
            mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)

    def test_constraint_mode_none_sends_no_schema(self):
        d = {"constraint_mode": "none", "temperature": 0, "seed": 1, "max_tokens": 64}
        cfgs, hs = self.cfgs(a={"decoding": d})
        s = mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)
        s.extract(A_INPUT)
        kw = self.llms[0].calls[0][1].kw
        self.assertNotIn("structured_outputs", kw)

    def test_garbage_makes_extract_fail_without_inventing_a_value(self):
        cfgs, hs = self.cfgs()
        s = mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)
        self.llms[0].texts = ["x", "y", "z"]
        with self.assertRaises(mr.ModelOutputInvalid) as cm:
            s.extract(A_INPUT)
        self.assertEqual((cm.exception.attempts, s.last_call_info.outcome), (3, "invalid"))

    def test_respond_sends_no_schema_and_a_package_prompt_with_the_b_adapter(self):
        cfgs, hs = self.cfgs()
        s = mr.load(self.ad, cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, system_prompts=refs(self.ad), backend=self.b)
        self.llms[0].texts = [GOOD_B]
        self.assertEqual(s.respond(B_INPUT).text, GOOD_B)
        (prompts, sp, lora), = self.llms[0].calls
        self.assertNotIn("structured_outputs", sp.kw)
        self.assertEqual(lora.name, "LORA_B")
        self.assertIn("LORA_B 시스템 프롬프트", prompts[0])
        self.assertNotIn("LORA_A 시스템 프롬프트", prompts[0])


if __name__ == "__main__":
    unittest.main()


class Bind(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.llms = []
        self.b = mr.VLLMBackend(None, profile={"base_model": "Qwen/Qwen2.5-1.5B-Instruct", "engine": {"max_lora_rank": 16}},
                                _import=fake_importer(llm_holder=self.llms))

    def make(self, name, **kw):
        Path(self._t.name, name).mkdir(); return adapters(Path(self._t.name) / name, **kw)

    def test_engine_only_then_bind_shares_engine_with_unique_ids(self):
        b1, b2 = self.b.bind(self.make("r1")), self.b.bind(self.make("r2"))
        self.assertEqual(len(self.llms), 1)
        self.assertEqual(len({b1.build_lora_request(r).int_id for r in ("LORA_A", "LORA_B")} |
                             {b2.build_lora_request(r).int_id for r in ("LORA_A", "LORA_B")}), 4)
        with self.assertRaises(mr.ContractViolation):
            self.b.build_lora_request("LORA_A")   # 묶기 전

    def test_bind_prechecks_rank_and_base(self):
        with self.assertRaises(mr.UnsupportedServingConfig):
            self.b.bind(self.make("hi", rank=32))
        with self.assertRaises(mr.UnsupportedServingConfig):
            self.b.bind(self.make("other", base="other/model"))
