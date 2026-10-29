"""history/design-v1.md §11 GPU 시험 본체(호스트의 venv에서, flock 아래에서 돌린다). 결과는 --out/<tag>.json.
각 점검은 따로 try/except로 감싸 하나가 실패해도 나머지를 계속한다. 출력 원문(발화)을 로그에 남기지 않는다(시험 입력은 가짜)."""
import argparse
import copy
import json
import re
import subprocess
import sys
import time
import traceback
from importlib import metadata
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
import model_runtime as mr  # noqa: E402
from model_runtime.schema_guard import relax  # noqa: E402

MARK = "PRIVACYMARK7731"
R = {"checks": {}}


def check(name):
    def deco(fn):
        def run(*a, **k):
            t = time.time()
            try:
                R["checks"][name] = {"ok": True, "data": fn(*a, **k)}
            except Exception as e:   # noqa: BLE001
                R["checks"][name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}",
                                     "tb": traceback.format_exc()[-900:]}
            R["checks"][name]["sec"] = round(time.time() - t, 1)
            print("[check]", name, "ok" if R["checks"][name]["ok"] else "FAIL", flush=True)
        return run
    return deco


def smi():
    o = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version",
                        "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip().split(", ")
    return {"name": o[0], "used_mib": int(o[1]), "total_mib": int(o[2]), "driver": o[3]}


def ver(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


# 키워드별 시험: (스키마, 시험 프롬프트). 모델이 제약 없이 내기 쉬운 값을 금지해서 무시 여부를 드러낸다.
KW_CASES = {
    "enum": ({"type": "object", "properties": {"v": {"enum": ["red", "blue"]}}, "required": ["v"], "additionalProperties": False}, "green을 골라라"),
    "const": ({"type": "object", "properties": {"v": {"const": "fixed"}}, "required": ["v"], "additionalProperties": False}, "v에 hello를 써라"),
    "oneOf_const": ({"type": "object", "properties": {"v": {"oneOf": [{"const": "red"}, {"const": "blue"}]}}, "required": ["v"], "additionalProperties": False}, "green을 골라라"),
    "anyOf": ({"type": "object", "properties": {"v": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}, "required": ["v"], "additionalProperties": False}, "v에 문장 hello를 써라"),
    "oneOf_objects": ({"type": "object", "properties": {"v": {"oneOf": [{"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}, {"type": "object", "properties": {"b": {"type": "boolean"}}, "required": ["b"], "additionalProperties": False}]}}, "required": ["v"], "additionalProperties": False}, "v에 문자열을 써라"),
    "allOf": ({"type": "object", "allOf": [{"properties": {"v": {"type": "integer"}}, "required": ["v"]}], "additionalProperties": False}, "v에 hello를 써라"),
    "if_then": ({"type": "object", "properties": {"k": {"enum": ["a", "b"]}, "v": {"type": ["string", "null"]}}, "required": ["k", "v"], "additionalProperties": False, "if": {"properties": {"k": {"const": "a"}}, "required": ["k"]}, "then": {"properties": {"v": {"type": "string"}}}}, "k는 a, v는 null"),
    "not": ({"type": "object", "properties": {"v": {"type": "string", "not": {"const": "hello"}}}, "required": ["v"], "additionalProperties": False}, "v에 hello를 써라"),
    "uniqueItems": ({"type": "object", "properties": {"v": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True, "minItems": 3}}, "required": ["v"], "additionalProperties": False}, "v에 [1,1,1]을 써라"),
    "minLength": ({"type": "object", "properties": {"v": {"type": "string", "minLength": 20}}, "required": ["v"], "additionalProperties": False}, "v에 a를 써라"),
    "minimum": ({"type": "object", "properties": {"v": {"type": "integer", "minimum": 1000}}, "required": ["v"], "additionalProperties": False}, "v에 1을 써라"),
    "additionalProperties_false": ({"type": "object", "properties": {"v": {"type": "integer"}}, "required": ["v"], "additionalProperties": False}, "v와 w와 z를 써라"),
    "type_array": ({"type": "object", "properties": {"v": {"type": ["integer", "null"]}}, "required": ["v"], "additionalProperties": False}, "v에 hello를 써라"),
    "ref_defs": ({"type": "object", "properties": {"v": {"$ref": "#/$defs/E"}}, "required": ["v"], "additionalProperties": False, "$defs": {"E": {"enum": ["red", "blue"]}}}, "green을 골라라"),
}


def build(args):
    prof = mr.load_profile(args.profile)
    import os
    for k, v in prof.get("env", {}).items():
        if not k.startswith("_"):
            os.environ[k] = str(v)
    ad = {}
    if not args.mock:
        from make_tiny_lora import make
        base = prof["base_model"]
        rank = prof.get("probe", {}).get("tiny_lora_rank", 8)
        ad = {"LORA_A": make(Path(args.out) / "adapters" / "A", base, rank, 1), "LORA_B": make(Path(args.out) / "adapters" / "B", base, rank, 2)}
    return prof, ad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--tag", default="run")
    ap.add_argument("--profile", required=True); ap.add_argument("--schema-dir", required=True)
    ap.add_argument("--no-lora", action="store_true", help="VRAM 기준선: LoRA를 켜지 않은 엔진")
    ap.add_argument("--mock", action="store_true", help="vLLM 없이 스크립트 논리만 확인(tests의 모킹)")
    args = ap.parse_args()
    Path(args.out).mkdir(parents=True, exist_ok=True)
    R.update(tag=args.tag, no_lora=args.no_lora, versions={p: ver(p) for p in ("vllm", "xgrammar", "torch", "transformers", "outlines", "llguidance", "peft")},
             gpu_before=None if args.mock else smi())
    prof, ad = build(args)
    schema_dir = Path(args.schema_dir)
    schemas = mr.SchemaSet(schema_dir)
    if args.mock:
        import tempfile, test_vllm_backend as tv
        t = tempfile.mkdtemp(); ad = tv.adapters(t)
        backend = mr.VLLMBackend(ad, profile=prof, _import=tv.fake_importer())
    else:
        p2 = copy.deepcopy(prof)
        if args.no_lora:
            p2["engine"].update(enable_lora=False, max_loras=1)
        backend = mr.VLLMBackend(ad, profile=p2) if not args.no_lora else None
        if args.no_lora:   # 기준선: LoRA 없는 엔진을 직접 만든다
            from vllm import LLM
            backend = type("B", (), {})()
            e = {k: v for k, v in p2["engine"].items() if k not in ("max_lora_rank", "max_loras", "enable_lora")}
            backend._llm = LLM(model=prof["base_model"], **e)
            R["gpu_after_load"] = smi()
            R["note"] = "기준선: LoRA 비활성"
            Path(args.out, f"{args.tag}.json").write_text(json.dumps(R, indent=1, ensure_ascii=False))
            return
    R["gpu_after_load"] = None if args.mock else smi()
    R["backend"] = {"name": backend.name, "version": backend.version, "engine_args": backend.engine_args, "base_model": backend.base_model}
    llm = backend._llm

    def req(role, schema, seed=1000, temp=0.0, mt=96, prompt="JSON으로만 답하라. ", mode="constrained"):
        return mr.GenerationRequest(role, prompt, schema, {"constraint_mode": mode, "temperature": temp, "seed": 0, "max_tokens": mt}, seed, 0)

    def gen(*a, **k):
        return backend.generate(req(*a, **k))

    # 2.0.0: JSON을 내는 역할은 LORA_A(ActionExtraction)뿐이다. LORA_B는 문장 그대로의 텍스트라 출력 스키마가 없다.
    # 이 점검은 v1(Qwen2.5-1.5B, 무작위 LoRA) 시험을 v2 API에 맞춘 것이고 GPU가 없는 PC에서는 --mock으로 논리만 확인했다(실제 vLLM에서는 미시험).
    par_schema = schemas.model_output_schema("LORA_A")
    gen_schema = par_schema   # 역할과 무관한 엔진 점검(최대 토큰, 결정성)에 쓰는 스키마

    @check("2_lora_load_select")
    def _():
        out = {}
        sch = {"type": "object", "properties": {"t": {"type": "string"}}, "required": ["t"]}
        out["A"] = gen("LORA_A", sch, prompt="안녕하세요 오늘은", temp=0)
        out["B"] = gen("LORA_B", sch, prompt="안녕하세요 오늘은", temp=0)
        from vllm import SamplingParams
        out["base"] = llm.generate(["안녕하세요 오늘은"], SamplingParams(temperature=0, max_tokens=32, seed=1), use_tqdm=False)[0].outputs[0].text
        out["A_differs_from_B"] = out["A"] != out["B"]
        out["adapters_change_output_vs_base"] = out["A"] != out["base"] or out["B"] != out["base"]
        return out
    _()

    @check("3_lora_plus_structured")
    def _():
        res = {}
        for role, sch in (("LORA_A", par_schema),):
            relaxed, removed = relax(sch, backend.supported_features)
            txt = gen(role, relaxed, temp=0.7, mt=256)
            try:
                v = json.loads(txt)
                res[role] = {"json": True, "removed": removed, "violations": schemas.output_violations(role, v)}
            except ValueError:
                res[role] = {"json": False, "removed": removed}
        txt = gen("LORA_B", None, temp=0.7, mt=64, prompt="안녕하세요 오늘은", mode="none")   # LORA_B: 제약 없는 텍스트
        res["LORA_B"] = {"text_len": len(txt), "violations": schemas.output_violations("LORA_B", txt.strip())}
        return res
    _()

    @check("4_5_keyword_matrix")
    def _():
        table = {}
        for kw, (sch, prompt) in KW_CASES.items():
            row = {}
            try:
                bad = 0; trunc = 0; n = 6
                for s in range(n):
                    txt = gen("LORA_A", sch, seed=s, temp=1.0, mt=96, prompt=f"{prompt}\n")
                    try:
                        obj = json.loads(txt)
                    except ValueError:
                        trunc += 1   # 닫히지 않은 JSON(잘림). 키워드 판정에서 뺀다
                        continue
                    if list(jsonschema.Draft202012Validator(sch).iter_errors(obj)):
                        bad += 1
                row = {"status": "enforced" if bad == 0 else "ignored_or_partial", "violations": f"{bad}/{n - trunc}", "unparsable": trunc}
            except Exception as e:   # noqa: BLE001
                row = {"status": "rejected", "error": f"{type(e).__name__}: {str(e)[:200]}"}
            table[kw] = row
        return table
    _()

    @check("4b_real_schemas")
    def _():
        res = {}
        for role, sch in (("LORA_A", par_schema),):
            row = {}
            for label, s in (("raw", sch), ("relaxed", relax(sch, backend.supported_features)[0])):
                try:
                    ok = 0; n = 8; viol = {}
                    for sd in range(n):
                        txt = gen(role, s, seed=sd, temp=0.9, mt=400)
                        try:
                            v = json.loads(txt)
                            vs = schemas.output_violations(role, v)
                        except ValueError:
                            vs = [("", "json")]
                        if not vs:
                            ok += 1
                        for _p, k in vs:
                            viol[k] = viol.get(k, 0) + 1
                    row[label] = {"valid": f"{ok}/{n}", "violation_keywords": viol}
                except Exception as e:   # noqa: BLE001
                    row[label] = {"rejected": f"{type(e).__name__}: {str(e)[:300]}"}
            res[role] = row
        return res
    _()

    @check("6_seed_repro")
    def _():
        sch = {"type": "object", "properties": {"t": {"type": "string", "maxLength": 30}}, "required": ["t"]}
        g = lambda seed, temp, role="LORA_B": gen(role, sch, seed=seed, temp=temp, mt=48, prompt="이야기를 한 줄 써라 ")
        return {
            "t0.8_same_seed_equal": g(5, 0.8) == g(5, 0.8),
            "t0.8_seed_plus_attempt_differs": len({g(5 + k, 0.8) for k in range(4)}),
            "t0_same_seed_equal": g(5, 0) == g(5, 0),
            "t0_seeds_differ": len({g(5 + k, 0) for k in range(4)}),
            "A_t0.8_same_seed_equal": g(5, 0.8, "LORA_A") == g(5, 0.8, "LORA_A"),
        }
    _()

    @check("7_max_tokens")
    def _():
        from vllm import SamplingParams
        sp = backend.build_sampling_params(req("LORA_B", relax(gen_schema, backend.supported_features)[0], mt=6))
        o = llm.generate(["JSON으로만 답하라. "], sp, lora_request=backend.build_lora_request("LORA_B"), use_tqdm=False)[0].outputs[0]
        try:
            json.loads(o.text); closed = True
        except ValueError:
            closed = False
        return {"finish_reason": o.finish_reason, "json_closed": closed, "text_len": len(o.text)}
    _()

    @check("8_latency")
    def _():
        s = relax(par_schema, backend.supported_features)[0]
        ts = []
        for i in range(12):
            t = time.time(); gen("LORA_A", s, seed=i, temp=0.3, mt=96, prompt=f"{MARK} 입력 {i} "); ts.append(time.time() - t)
        rest = sorted(ts[1:])
        return {"first_s": round(ts[0], 2), "p50_s": round(rest[len(rest) // 2], 3), "p95_s": round(rest[int(len(rest) * .95) - 1], 3), "n": len(rest)}
    _()

    @check("9_determinism_outputs")   # gpu_report.py가 run1과 run2를 비교한다
    def _():
        sch = relax(gen_schema, backend.supported_features)[0]
        return [gen("LORA_B", sch, seed=100 + i, temp=0.8, mt=64, prompt=f"안녕 {i} ") for i in range(4)]
    _()

    @check("10_conformance_via_load")
    def _():
        from boundary_contracts.canonical import file_hash
        from helpers import A_INPUT, B_INPUT
        cfgs = mr.fake_serving_configs(backend)
        hs = {r: mr.serving_config_hash(c) for r, c in cfgs.items()}
        refs = {}
        for r, d in ad.items():   # 시험 어댑터에 패키지 프롬프트 자리표시 파일을 둔다(학습과 짝인 프롬프트가 아니다)
            (Path(d) / "system_prompt.txt").write_text(f"({r} 점검용 자리표시 시스템 프롬프트)\n", encoding="utf-8")
            refs[r] = {"path": "system_prompt.txt", "sha256": file_hash(Path(d) / "system_prompt.txt")}
        s = mr.load(ad, cfgs, schema_dir=schema_dir, expected_serving_config_hashes=hs, system_prompts=refs, backend=backend)
        try:
            pa = s.extract(A_INPUT, {"order": [A_INPUT["question_id"]], "skips": []}, {"fields": [{"slot": A_INPUT["question_id"] + ".placeholder", "data_type": "boolean"}]});   # 3.0.0: flow·fields 필수. 점검용 한 문항 흐름과 자리표시 규격
            sa = schemas.output_violations("LORA_A", pa); ia = s.last_call_info
            ra = {"attempts": ia.attempts, "outcome": ia.outcome, "schema_violations": len(sa)}
        except mr.ModelOutputInvalid as e:   # 무작위 LoRA라 형식을 못 지킬 수 있다. 값을 지어내지 않는 것이 정상 동작이다
            ra = {"attempts": e.attempts, "outcome": "invalid", "schema_violations": None}
        pb = s.respond(B_INPUT); ib = s.last_call_info
        return {"extract": ra,
                "respond": {"attempts": ib.attempts, "outcome": ib.outcome, "is_fallback": pb.is_fallback,
                            "schema_violations": len(schemas.output_violations("LORA_B", pb.text))}}
    _()

    @check("2b_rank_ceiling")   # 엔진이 죽는 것을 확인하는 시험이라 맨 마지막에 둔다
    def _():
        if args.mock:
            return "mock"
        from make_tiny_lora import make
        d = make(Path(args.out) / "adapters" / "rank32", prof["base_model"], 32, 3)
        from vllm.lora.request import LoRARequest
        from vllm import SamplingParams
        try:
            llm.generate(["x"], SamplingParams(max_tokens=4), lora_request=LoRARequest("r32", 9, str(d)), use_tqdm=False)
            return {"max_lora_rank": prof["engine"].get("max_lora_rank"), "rank32": "accepted(unexpected)"}
        except Exception as e:   # noqa: BLE001
            return {"max_lora_rank": prof["engine"].get("max_lora_rank"), "rank32": f"rejected: {type(e).__name__}: {str(e)[:200]}"}
    _()

    R["gpu_end"] = None if args.mock else smi()
    Path(args.out, f"{args.tag}.json").write_text(json.dumps(R, indent=1, ensure_ascii=False))
    print("완료", args.tag)


if __name__ == "__main__":
    main()
