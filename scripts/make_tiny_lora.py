"""시험용 무작위 LoRA 어댑터를 만든다(품질 없음, 로딩·선택 경로 확인용). torch + safetensors 필요.
사용: make_tiny_lora.py OUT_DIR --base Qwen/Qwen2.5-1.5B-Instruct --rank 8 --seed 1"""
import argparse
import json
from pathlib import Path


def make(out: Path, base: str, rank: int, seed: int, targets=("q_proj", "v_proj"), scale=0.02) -> Path:
    import torch
    from safetensors.torch import save_file
    from transformers import AutoConfig   # vllm 의존으로 설치되어 있다. 가중치는 내려받지 않는다.
    cfg = AutoConfig.from_pretrained(base)
    h, layers = cfg.hidden_size, cfg.num_hidden_layers
    kv = cfg.num_key_value_heads * (h // cfg.num_attention_heads)
    out_dim = {"q_proj": h, "k_proj": kv, "v_proj": kv, "o_proj": h}
    g = torch.Generator().manual_seed(seed)
    t = {}
    for i in range(layers):
        for m in targets:
            pre = f"base_model.model.model.layers.{i}.self_attn.{m}"
            t[f"{pre}.lora_A.weight"] = (torch.randn(rank, h, generator=g) * scale).to(torch.bfloat16)
            t[f"{pre}.lora_B.weight"] = (torch.randn(out_dim[m], rank, generator=g) * scale).to(torch.bfloat16)
    out.mkdir(parents=True, exist_ok=True)
    save_file(t, str(out / "adapter_model.safetensors"))
    (out / "adapter_config.json").write_text(json.dumps({
        "peft_type": "LORA", "task_type": "CAUSAL_LM", "base_model_name_or_path": base, "r": rank,
        "lora_alpha": rank * 2, "lora_dropout": 0.0, "bias": "none", "target_modules": list(targets),
        "inference_mode": True}, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--rank", type=int, default=8); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(make(Path(a.out), a.base, a.rank, a.seed))
