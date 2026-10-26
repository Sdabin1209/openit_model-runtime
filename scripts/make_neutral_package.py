#!/usr/bin/env python3
"""학습 없이 쓰는 '효과 없는 LoRA' 모델 패키지(LORA_A, LORA_B)를 결정적으로 만든다. 시험·데모용 대체이며 학습된 어댑터가 아니다.

어댑터: PEFT 형식(adapter_config.json + adapter_model.safetensors). lora_B를 0으로 두므로 출력은 기반 모델과 같다.
lora_A는 시드에서 결정적으로 만든다. 의존은 표준 라이브러리뿐이다(safetensors 파일을 직접 쓴다; torch·vLLM 불필요).
패키지: ModelPackageManifest(boundary-contracts 2.0.0)와 어댑터 디렉터리. 시스템 프롬프트 파일(system_prompt.txt)은 어댑터 디렉터리 안에 두고
manifest.system_prompt {path, sha256}가 가리킨다(D75). 이 파일의 문장은 학습과 짝인 프롬프트가 아니라 시험용 자리표시자다. 같은 인자면 같은 바이트·같은 해시.
호출은 PYTHONPATH에 src가 있거나 model-runtime이 설치된 환경에서(boundary_contracts의 해시 구현을 쓴다).

사용: make_neutral_package.py OUT_DIR [--profile profiles/gpu-12g.json] [--vllm-version 0.31.0] [--rank 8]
  [--revision main] [--targets q_proj,v_proj] [--seed 0] [--model-config HF config.json] [--max-tokens 512]
  [--system-prompt-a FILE] [--system-prompt-b FILE]
--system-prompt-a/-b: 역할별 시스템 프롬프트 파일(UTF-8, 비어 있지 않음). 내용을 그대로(바이트 단위) 어댑터 디렉터리의 system_prompt.txt로 복사하고 manifest.system_prompt {path, sha256}를
  그 파일의 해시로 만든다. 주지 않으면 자리표시 문장을 쓴다.
결과: OUT_DIR/LORA_A/{adapter/,manifest.json}, OUT_DIR/LORA_B/{adapter/,manifest.json}, OUT_DIR/SUMMARY.json
기반 모델의 크기(은닉 차원 등)는 알려진 모델은 내장값, 그 밖은 --model-config(HF config.json)로 받는다.
Qwen3처럼 head_dim이 은닉 차원/헤드 수와 다른 모델은 config.json의 head_dim을 쓴다. Qwen3 값은 내장하지 않는다(확인하지 못한 수치를 넣지 않기 위해 --model-config가 필요하다)."""
from __future__ import annotations

import argparse
import json
import math
import random
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # noqa: E402
from boundary_contracts.canonical import content_hash, directory_hash, file_hash, sha256_hash  # noqa: E402

import model_runtime as mr  # noqa: E402

CREATED_AT = "2026-01-01T00:00:00Z"   # 결정성을 위해 고정(실제 생성 시각이 아니다)
KNOWN_MODELS = {   # 은닉 차원, 층 수, 어텐션 헤드, KV 헤드 (Hugging Face config.json의 값)
    "Qwen/Qwen2.5-1.5B-Instruct": {"hidden_size": 1536, "num_hidden_layers": 28, "num_attention_heads": 12, "num_key_value_heads": 2},
}
ROLE_IDS = {"LORA_A": "lora-a-neutral", "LORA_B": "lora-b-neutral"}
VERSION = "0.0.1-neutral"
NEUTRAL_SYSTEM_PROMPTS = {   # 시험용 자리표시자. 학습한 시스템 프롬프트가 아니다(실제 패키지는 model-lab이 학습과 짝인 파일을 넣는다)
    "LORA_A": "(중립 시험 패키지의 자리표시 시스템 프롬프트: LoRA-A. 학습과 짝인 프롬프트가 아니다)\n",
    "LORA_B": "(중립 시험 패키지의 자리표시 시스템 프롬프트: LoRA-B. 학습과 짝인 프롬프트가 아니다)\n",
}
NOTE = ("학습하지 않은 시험용 대체 어댑터다. lora_B가 0이라 출력은 기반 모델과 같다. 성능 판단 근거가 아니며 "
        "릴리스 후보로 쓰지 않는다.")


def model_dims(base_model: str, config_path: str | None) -> dict:
    if config_path:
        c = json.loads(Path(config_path).read_text(encoding="utf-8"))
    elif base_model in KNOWN_MODELS:
        c = KNOWN_MODELS[base_model]
    else:
        raise SystemExit(f"{base_model}의 모델 차원을 모른다: --model-config로 Hugging Face config.json을 준다")
    d = {k: int(c[k]) for k in ("hidden_size", "num_hidden_layers", "num_attention_heads", "num_key_value_heads")}
    d["head_dim"] = int(c.get("head_dim") or d["hidden_size"] // d["num_attention_heads"])
    return d


def _f16(values) -> bytes:
    return b"".join(struct.pack("<e", v) for v in values)


def write_safetensors(path: Path, tensors: dict[str, tuple[tuple[int, int], bytes]]) -> None:
    """tensors: 이름 → (shape, little-endian float16 바이트). 이름 순으로 써서 결정적이다."""
    header, off = {"__metadata__": {"format": "pt"}}, 0
    for name in sorted(tensors):
        shape, data = tensors[name]
        header[name] = {"dtype": "F16", "shape": list(shape), "data_offsets": [off, off + len(data)]}
        off += len(data)
    h = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    h += b" " * (-len(h) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h)))
        f.write(h)
        for name in sorted(tensors):
            f.write(tensors[name][1])


def make_adapter(out: Path, base_model: str, dims: dict, rank: int, targets: list[str], seed: int) -> None:
    h = dims["hidden_size"]
    q = dims["num_attention_heads"] * dims["head_dim"]
    kv = dims["num_key_value_heads"] * dims["head_dim"]
    out_dim = {"q_proj": q, "k_proj": kv, "v_proj": kv, "o_proj": h}
    in_dim = {"q_proj": h, "k_proj": h, "v_proj": h, "o_proj": q}
    rng = random.Random(seed)
    bound = 1.0 / math.sqrt(h)
    tensors = {}
    for i in range(dims["num_hidden_layers"]):
        for m in targets:
            pre = f"base_model.model.model.layers.{i}.self_attn.{m}"
            tensors[f"{pre}.lora_A.weight"] = ((rank, in_dim[m]), _f16(rng.uniform(-bound, bound) for _ in range(rank * in_dim[m])))
            tensors[f"{pre}.lora_B.weight"] = ((out_dim[m], rank), _f16([0.0] * (out_dim[m] * rank)))
    out.mkdir(parents=True, exist_ok=True)
    write_safetensors(out / "adapter_model.safetensors", tensors)
    (out / "adapter_config.json").write_text(json.dumps({
        "peft_type": "LORA", "task_type": "CAUSAL_LM", "base_model_name_or_path": base_model, "r": rank,
        "lora_alpha": rank, "lora_dropout": 0.0, "bias": "none", "target_modules": targets,
        "inference_mode": True}, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def build(out: Path, *, profile_path: Path, vllm_version: str, rank: int, revision: str, targets: list[str], seed: int,
          model_config: str | None, max_tokens: int, system_prompts: dict[str, bytes] | None = None) -> dict:
    prof = mr.load_profile(profile_path)
    base_model = prof.get("base_model")
    if not base_model:
        raise SystemExit("프로파일에 base_model이 없다")
    max_rank = int(prof.get("engine", {}).get("max_lora_rank", 16))
    if rank > max_rank:
        raise SystemExit(f"순위 {rank}이 프로파일의 max_lora_rank({max_rank})보다 크다")
    dims = model_dims(base_model, model_config)
    backend = SimpleNamespace(name="vllm", version=vllm_version, supported_features=frozenset(mr.DEFAULT_SUPPORTED_FEATURES))
    summary = {}
    for role, pid in ROLE_IDS.items():
        d = out / role
        make_adapter(d / "adapter", base_model, dims, rank, targets, seed + (0 if role == "LORA_A" else 1))
        sp = (system_prompts or {}).get(role)
        (d / "adapter" / "system_prompt.txt").write_bytes(sp if sp is not None else NEUTRAL_SYSTEM_PROMPTS[role].encode("utf-8"))   # 해시 계산 전에 쓴다
        cfg = mr.fake_serving_config(backend)
        # LORA_B 출력은 JSON이 아니라 문장이라 제약 디코딩을 쓰지 않는다
        cfg["decoding"] = {"constraint_mode": "constrained" if role == "LORA_A" else "none", "temperature": 0, "retry_temperature": 0.7,
                           "seed": 1000, "max_tokens": max_tokens}
        m = {
            "pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "package_id": pid, "version": VERSION, "role": role,
            "created_at": CREATED_AT, "runtime_contract_version": mr.SUPPORTED_RUNTIME_CONTRACT_VERSION,
            "base_model": {"id": base_model, "revision": revision},
            "adapter_hash": directory_hash(d / "adapter"),
            "serving_config": cfg, "serving_config_hash": sha256_hash(cfg),
            "system_prompt": {"path": "system_prompt.txt", "sha256": file_hash(d / "adapter" / "system_prompt.txt")},
            "lineage": {"dataset": {"id": "neutral-no-dataset", "version": "0", "content_hash": sha256_hash({"neutral": True})},
                        "training_run_id": "neutral-no-training", "turn_engine_version": "none",
                        "model_runtime_version": mr.RUNTIME_VERSION},
            "eval_report_ref": "neutral-no-eval",
            "ext": {"model-runtime": {"neutral": True, "trained": False, "note": NOTE, "rank": rank, "targets": targets, "seed": seed}},
        }
        m["content_hash"] = content_hash(m)
        (d / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        summary[role] = {"package_id": pid, "adapter_hash": m["adapter_hash"], "serving_config_hash": m["serving_config_hash"],
                         "content_hash": m["content_hash"]}
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "gpu-12g.json"), help="기본 gpu-12g는 Qwen3-4B라 --model-config가 필요하다")
    ap.add_argument("--vllm-version", default="0.31.0", help="serving_config.constraint.backend_version(서빙 이미지의 vLLM과 같아야 한다)")
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--revision", default="main")
    ap.add_argument("--targets", default="q_proj,v_proj")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model-config")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--system-prompt-a", help="LORA_A 시스템 프롬프트 파일(없으면 자리표시 문장)")
    ap.add_argument("--system-prompt-b", help="LORA_B 시스템 프롬프트 파일(없으면 자리표시 문장)")
    a = ap.parse_args()
    prompts = {}
    for role, f in (("LORA_A", a.system_prompt_a), ("LORA_B", a.system_prompt_b)):
        if f:
            try:
                data = Path(f).read_bytes()
                text = data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                raise SystemExit(f"{role} 시스템 프롬프트를 읽지 못했다({f}): {type(e).__name__}")
            if not text.strip():
                raise SystemExit(f"{role} 시스템 프롬프트가 비어 있다: {f}")
            prompts[role] = data
    s = build(Path(a.out), profile_path=Path(a.profile), vllm_version=a.vllm_version, rank=a.rank, revision=a.revision,
              targets=a.targets.split(","), seed=a.seed, model_config=a.model_config, max_tokens=a.max_tokens, system_prompts=prompts)
    print(json.dumps(s, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
