#!/usr/bin/env python3
"""학습 없이 쓰는 '효과 없는 LoRA' 모델 패키지(LORA_A, LORA_B)를 결정적으로 만든다. 시험·데모용 대체이며 학습된 어댑터가 아니다.

어댑터: PEFT 형식(adapter_config.json + adapter_model.safetensors). lora_B를 0으로 두므로 출력은 기반 모델과 같다.
lora_A는 시드에서 결정적으로 만든다. 의존은 표준 라이브러리뿐이다(safetensors 파일을 직접 쓴다; torch·vLLM 불필요).
패키지: ModelPackageManifest(boundary-contracts)와 어댑터 디렉터리. 같은 인자면 같은 바이트·같은 해시.
호출은 PYTHONPATH에 src가 있거나 model-runtime이 설치된 환경에서(boundary_contracts의 해시 구현을 쓴다).

사용: make_neutral_package.py OUT_DIR [--profile profiles/gpu-12g.json] [--vllm-version 0.31.0] [--rank 8]
  [--revision main] [--targets q_proj,v_proj] [--seed 0] [--model-config HF config.json] [--max-tokens 512]
결과: OUT_DIR/LORA_A/{adapter/,manifest.json}, OUT_DIR/LORA_B/{adapter/,manifest.json}, OUT_DIR/SUMMARY.json
기반 모델의 크기(은닉 차원 등)는 알려진 모델은 내장값, 그 밖은 --model-config(HF config.json)로 받는다."""
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
from boundary_contracts.canonical import content_hash, directory_hash, sha256_hash  # noqa: E402

import model_runtime as mr  # noqa: E402

CREATED_AT = "2026-01-01T00:00:00Z"   # 결정성을 위해 고정(실제 생성 시각이 아니다)
KNOWN_MODELS = {   # 은닉 차원, 층 수, 어텐션 헤드, KV 헤드 (Hugging Face config.json의 값)
    "Qwen/Qwen2.5-1.5B-Instruct": {"hidden_size": 1536, "num_hidden_layers": 28, "num_attention_heads": 12, "num_key_value_heads": 2},
}
ROLE_IDS = {"LORA_A": "lora-a-neutral", "LORA_B": "lora-b-neutral"}
VERSION = "0.0.1-neutral"
NOTE = ("학습하지 않은 시험용 대체 어댑터다. lora_B가 0이라 출력은 기반 모델과 같다. 성능 판단 근거가 아니며 "
        "릴리스 후보로 쓰지 않는다.")


def model_dims(base_model: str, config_path: str | None) -> dict:
    if config_path:
        c = json.loads(Path(config_path).read_text(encoding="utf-8"))
    elif base_model in KNOWN_MODELS:
        c = KNOWN_MODELS[base_model]
    else:
        raise SystemExit(f"{base_model}의 모델 차원을 모른다: --model-config로 Hugging Face config.json을 준다")
    return {k: int(c[k]) for k in ("hidden_size", "num_hidden_layers", "num_attention_heads", "num_key_value_heads")}


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
    kv = dims["num_key_value_heads"] * (h // dims["num_attention_heads"])
    out_dim = {"q_proj": h, "k_proj": kv, "v_proj": kv, "o_proj": h}
    rng = random.Random(seed)
    bound = 1.0 / math.sqrt(h)
    tensors = {}
    for i in range(dims["num_hidden_layers"]):
        for m in targets:
            pre = f"base_model.model.model.layers.{i}.self_attn.{m}"
            tensors[f"{pre}.lora_A.weight"] = ((rank, h), _f16(rng.uniform(-bound, bound) for _ in range(rank * h)))
            tensors[f"{pre}.lora_B.weight"] = ((out_dim[m], rank), _f16([0.0] * (out_dim[m] * rank)))
    out.mkdir(parents=True, exist_ok=True)
    write_safetensors(out / "adapter_model.safetensors", tensors)
    (out / "adapter_config.json").write_text(json.dumps({
        "peft_type": "LORA", "task_type": "CAUSAL_LM", "base_model_name_or_path": base_model, "r": rank,
        "lora_alpha": rank, "lora_dropout": 0.0, "bias": "none", "target_modules": targets,
        "inference_mode": True}, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def build(out: Path, *, profile_path: Path, vllm_version: str, rank: int, revision: str, targets: list[str], seed: int,
          model_config: str | None, max_tokens: int) -> dict:
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
        cfg = mr.fake_serving_config(backend)
        cfg["decoding"] = {"constraint_mode": "constrained", "temperature": 0, "retry_temperature": 0.7, "seed": 1000,
                           "max_tokens": max_tokens}
        m = {
            "pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "package_id": pid, "version": VERSION, "role": role,
            "created_at": CREATED_AT, "runtime_contract_version": mr.SUPPORTED_RUNTIME_CONTRACT_VERSION,
            "base_model": {"id": base_model, "revision": revision},
            "adapter_hash": directory_hash(d / "adapter"),
            "serving_config": cfg, "serving_config_hash": sha256_hash(cfg),
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
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "gpu-12g.json"))
    ap.add_argument("--vllm-version", default="0.31.0", help="serving_config.constraint.backend_version(서빙 이미지의 vLLM과 같아야 한다)")
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--revision", default="main")
    ap.add_argument("--targets", default="q_proj,v_proj")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model-config")
    ap.add_argument("--max-tokens", type=int, default=512)
    a = ap.parse_args()
    s = build(Path(a.out), profile_path=Path(a.profile), vllm_version=a.vllm_version, rank=a.rank, revision=a.revision,
              targets=a.targets.split(","), seed=a.seed, model_config=a.model_config, max_tokens=a.max_tokens)
    print(json.dumps(s, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
