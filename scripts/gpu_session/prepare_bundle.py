#!/usr/bin/env python3
"""학습된 LoRA A·B 어댑터 한 쌍과 C1 시스템 프롬프트로 model-runtime이 읽는 릴리스 디렉터리를 만든다(GPU 세션 키트, docs/GPU_SESSION.md).

결과(OUT/<release_id>/): manifest.json(ReleaseManifest, 계약 검증함), question_bank.json(빈 문항 은행: 이 측정은 문항을 쓰지 않는다),
adapters/LORA_A, adapters/LORA_B(adapter_config.json, adapter_model.safetensors, system_prompt.txt). OUT/PREPARE_REPORT.json에 어댑터 파일 해시,
시스템 프롬프트 종류 수, 기반 모델 이름, 서빙 설정 값을 남긴다. 이 디렉터리를 release_server.py가 I4(HTTP.md) 모양으로 내주면 서버가 PUT으로 적재한다.

원본 어댑터 디렉터리는 읽기만 한다(어댑터 파일 두 개를 OUT으로 복사). 시스템 프롬프트는 C1 jsonl 첫 행의 system 메시지를 바이트 그대로 쓴다(학습 글자와 한 글자도 달라서는 안 된다, ADR 0011).
호출은 PYTHONPATH에 model-runtime의 src와 boundary_contracts가 있는 환경에서(서버에서는 키트의 PYTHONPATH). 표준 라이브러리 외 의존은 이 둘뿐이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "src").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.append(str(ROOT / "src"))   # 키트 트리나 PYTHONPATH에 이미 있으면 그쪽이 우선한다

from boundary_contracts import PIPELINE_SCHEMA_VERSION, validator  # noqa: E402
from boundary_contracts.canonical import content_hash, directory_hash, file_hash, sha256_hash  # noqa: E402

import model_runtime as mr  # noqa: E402

ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
ROLES = ("LORA_A", "LORA_B")
CREATED_AT = "2026-01-01T00:00:00Z"   # 결정성을 위해 고정(실제 생성 시각이 아니다)


def system_prompts_of(path: Path) -> tuple[str, dict[str, int]]:
    """jsonl 전체를 훑어 첫 행의 system 메시지와 (종류별 행 수)를 낸다. 종류가 여럿이면 호출자가 보고한다."""
    first, kinds = None, {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                msgs = json.loads(line)["messages"]
            except (ValueError, KeyError, TypeError) as e:   # 불량 줄은 스택 트레이스 대신 파일·줄 번호로 알린다(임대 중 원인 파악 시간)
                raise SystemExit(f"{path}:{n}: JSON 행이 아니거나 messages가 없다({type(e).__name__})")
            sys_msgs = [m["content"] for m in msgs if m["role"] == "system"]
            if len(sys_msgs) != 1:
                raise SystemExit(f"{path}: system 메시지가 정확히 하나가 아닌 행이 있다")
            s = sys_msgs[0]
            first = s if first is None else first
            kinds[s] = kinds.get(s, 0) + 1
    if first is None:
        raise SystemExit(f"{path}: 행이 없다")
    return first, kinds


def check_adapter(src: Path, role: str, base_model: str, max_rank: int, rewrite_base: bool) -> dict:
    cfg_path = src / "adapter_config.json"
    for n in ADAPTER_FILES:
        if not (src / n).is_file():
            raise SystemExit(f"{role}: {src / n}이 없다")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    info = {"base_model_name_or_path": cfg.get("base_model_name_or_path"), "r": cfg.get("r"), "lora_alpha": cfg.get("lora_alpha"),
            "target_modules": cfg.get("target_modules"), "rewritten_base_model": False,
            "other_files_not_copied": sorted(p.name for p in src.iterdir() if p.name not in ADAPTER_FILES)}
    if not isinstance(cfg.get("r"), int) or cfg["r"] > max_rank:
        raise SystemExit(f"{role}: 순위 r={cfg.get('r')}가 프로파일 max_lora_rank({max_rank})보다 크거나 정수가 아니다")
    if cfg.get("base_model_name_or_path") != base_model:
        if not rewrite_base:
            raise SystemExit(f"{role}: adapter_config의 base_model_name_or_path({cfg.get('base_model_name_or_path')!r})가 프로파일의 기반 모델({base_model!r})과 다르다. "
                             "서버가 412로 거부한다. 같은 모델이 확실하면 --rewrite-base-name으로 복사본의 이름만 바꾼다(원본은 건드리지 않고 보고서에 남긴다)")
        info["rewritten_base_model"] = True
    for k in ("use_dora", "modules_to_save"):   # vLLM이 지원하지 않는 형태(적재 때 거부). 파드에서 알게 되면 늦다
        if cfg.get(k):
            raise SystemExit(f"{role}: adapter_config의 {k}={cfg.get(k)!r}는 vLLM LoRA가 지원하지 않는다")
    if cfg.get("rank_pattern") or cfg.get("alpha_pattern"):
        raise SystemExit(f"{role}: rank_pattern/alpha_pattern이 있는 어댑터는 vLLM이 모듈별 rank를 다르게 적재하지 못한다")
    info["safetensors"] = check_safetensors_header(src / "adapter_model.safetensors", role, cfg["r"], cfg.get("target_modules"))
    return info


LORA_TENSOR_RE = re.compile(r"(?:^|\.)([A-Za-z0-9_]+)\.lora_([AB])\.weight$")


def check_safetensors_header(path: Path, role: str, r: int, target_modules: list) -> dict:
    """safetensors 헤더(앞 8바이트 길이 + JSON)만 읽어 vLLM LoRA 적재 조건을 정적으로 대조한다. 텐서 데이터는 읽지 않는다.
    왜: 파드에서 적재가 실패해서야 알면 임대 시간이 든다. 헤더만으로 알 수 있는 것(rank 일치, lora_A/B 쌍, 대상 모듈, 파일 크기)은 지금 거른다.
    vLLM이 거부하는 어댑터 형태(LoRA 가중치가 아닌 텐서 = modules_to_save/임베딩 학습)도 여기서 막는다."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise SystemExit(f"{role}: {path.name}이 safetensors가 아니다(8바이트 미만)")
        n = struct.unpack("<Q", head)[0]
        if n > 100 * 1024 * 1024 or 8 + n > size:
            raise SystemExit(f"{role}: {path.name} 헤더 길이({n})가 비정상이다. safetensors가 아니거나 잘렸다")
        try:
            h = json.loads(f.read(n))
        except ValueError:
            raise SystemExit(f"{role}: {path.name} 헤더가 JSON이 아니다") from None
    h.pop("__metadata__", None)
    mods, pairs, dtypes, end = set(), {}, set(), 0
    for name, t in h.items():
        m = LORA_TENSOR_RE.search(name)
        if not m:
            raise SystemExit(f"{role}: LoRA 가중치가 아닌 텐서 {name!r}. vLLM은 modules_to_save/학습된 임베딩이 있는 어댑터를 적재하지 못한다")
        mods.add(m.group(1)); dtypes.add(t["dtype"]); end = max(end, t["data_offsets"][1])
        key = name[: m.start()] if m.start() else name.rsplit(".lora_", 1)[0]
        shp = t["shape"]
        want_rank = shp[0] if m.group(2) == "A" else shp[-1]
        if len(shp) != 2 or want_rank != r:
            raise SystemExit(f"{role}: 텐서 {name} shape {shp}의 rank가 adapter_config r={r}와 다르다")
        pairs.setdefault(key + "." + m.group(1), set()).add(m.group(2))
    lone = [k for k, v in pairs.items() if v != {"A", "B"}]
    if not h or lone:
        raise SystemExit(f"{role}: lora_A/lora_B 쌍이 맞지 않는다({lone[:3]}) 또는 텐서가 없다")
    if 8 + n + end != size:
        raise SystemExit(f"{role}: 파일 크기 {size}가 헤더가 말하는 크기 {8 + n + end}와 다르다(잘린 파일 의심)")
    extra = sorted(mods - set(target_modules or []))
    if extra:
        raise SystemExit(f"{role}: 텐서의 모듈 {extra}가 adapter_config target_modules에 없다")
    return {"tensors": len(h), "lora_modules": sorted(mods), "dtypes": sorted(dtypes), "data_bytes": end}


RELEASE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
OWNED_MARK = ".mr-session-owned"   # 이 도구가 만든 번들이라는 표식. --overwrite는 표식이 있는 폴더만 지운다


def build(a: argparse.Namespace) -> dict:
    prof = mr.load_profile(a.profile)
    base_model = prof.get("base_model")
    if not base_model:
        raise SystemExit("프로파일에 base_model이 없다")
    max_rank = int(prof.get("engine", {}).get("max_lora_rank", 16))
    if not RELEASE_ID_RE.fullmatch(a.release_id) or a.release_id in (".", ".."):   # 서버의 ID_RE와 같은 문자 집합. `..`이면 부모가 지워진다
        raise SystemExit("--release-id는 영문·숫자·`.`·`_`·`-`만 쓰고 `.`/`..`이 아니어야 한다")
    out = Path(a.out) / a.release_id
    if out.resolve().parent != Path(a.out).resolve():
        raise SystemExit("--release-id가 --out 바로 아래 폴더를 가리키지 않는다")
    if out.exists() or out.is_symlink():
        if not a.overwrite:
            raise SystemExit(f"{out}이 이미 있다. 덮어쓰려면 --overwrite (이 도구가 만든 번들만 지운다)")
        if out.is_symlink() or not (out / OWNED_MARK).is_file():
            raise SystemExit(f"{out}은 이 도구가 만든 번들이 아니다({OWNED_MARK} 표식 없음). 지우지 않는다")
        shutil.rmtree(out)
    if a.backend == "fake":
        backend = mr.FakeBackend()
    else:
        if not a.vllm_version:
            raise SystemExit("--vllm-version이 필요하다(서버의 vllm 버전; serving_config의 backend_version과 일치해야 적재된다)")
        backend = SimpleNamespace(name="vllm", version=a.vllm_version, supported_features=frozenset(mr.DEFAULT_SUPPORTED_FEATURES))
    srcs = {"LORA_A": Path(a.adapter_a), "LORA_B": Path(a.adapter_b)}
    c1 = {"LORA_A": Path(a.c1_a), "LORA_B": Path(a.c1_b)}
    max_tokens = {"LORA_A": a.max_tokens_a, "LORA_B": a.max_tokens_b}
    report = {"release_id": a.release_id, "base_model": base_model, "profile": str(a.profile), "backend": {"name": backend.name, "version": backend.version},
              "max_tokens": max_tokens, "roles": {}, "warnings": []}
    prompts, adapters = {}, {}
    for role in ROLES:
        first, kinds = system_prompts_of(c1[role])
        prompts[role] = first.encode("utf-8")
        r = {"system_prompt_kinds": len(kinds), "system_prompt_rows_by_kind": sorted(kinds.values(), reverse=True),
             "system_prompt_sha256": file_hash_bytes(prompts[role]), "system_prompt_chars": len(first)}
        if len(kinds) > 1:
            report["warnings"].append(f"{role}: C1 파일에 시스템 프롬프트가 {len(kinds)}종이다. 첫 행의 것을 썼다(행 수 {r['system_prompt_rows_by_kind']}). 학습 때 어느 것이 쓰였는지 확인해야 한다")
        r["adapter"] = check_adapter(srcs[role], role, base_model, max_rank, a.rewrite_base_name)
        report["roles"][role] = r
    out.mkdir(parents=True)
    (out / OWNED_MARK).write_text("scripts/gpu_session/prepare_bundle.py\n", encoding="utf-8")
    for role in ROLES:
        d = out / "adapters" / role
        d.mkdir(parents=True)
        for n in ADAPTER_FILES:
            shutil.copyfile(srcs[role] / n, d / n)
        if report["roles"][role]["adapter"]["rewritten_base_model"]:
            cfg = json.loads((d / "adapter_config.json").read_text(encoding="utf-8"))
            cfg["base_model_name_or_path"] = base_model
            (d / "adapter_config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (d / "system_prompt.txt").write_bytes(prompts[role])
    bank = {"version": 1, "questions": []}
    (out / "question_bank.json").write_text(json.dumps(bank), encoding="utf-8")
    ad = {}
    for role in ROLES:
        cfg = mr.fake_serving_config(backend)
        cfg["decoding"] = {"constraint_mode": "constrained" if role == "LORA_A" else "none", "temperature": 0, "retry_temperature": 0.7,
                           "seed": 1000, "max_tokens": max_tokens[role]}
        d = out / "adapters" / role
        ad[role] = {"adapter_hash": directory_hash(d), "serving_config": cfg, "serving_config_hash": sha256_hash(cfg),
                    "system_prompt": {"path": "system_prompt.txt", "sha256": file_hash(d / "system_prompt.txt")}}
        report["roles"][role]["adapter_files_sha256"] = {n: file_hash(d / n) for n in ADAPTER_FILES}
        report["roles"][role]["adapter_hash"] = ad[role]["adapter_hash"]
        report["roles"][role]["serving_config"] = cfg
    ref = lambda i, v: {"id": i, "version": v, "content_hash": sha256_hash({"gpu-session": i})}   # noqa: E731
    m = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": a.release_id, "created_at": CREATED_AT, "gate_decision_id": "gpu-session-no-gate",
         "question_set": {"id": "gpu-session-bank", "version": "1", "content_hash": sha256_hash(bank)},
         "lora_a": ref("lora-a-trained", "gpu-session"), "lora_b": ref("lora-b-trained", "gpu-session"),
         "runtime_contract_version": mr.SUPPORTED_RUNTIME_CONTRACT_VERSION, "turn_engine_version": a.turn_engine_version, "model_runtime_version": mr.RUNTIME_VERSION,
         "adapters": ad,
         "ext": {"model-runtime": {"gpu_session": True, "note": "GPU 세션 측정용 임시 릴리스. 게이트를 거치지 않았고 운영 릴리스가 아니다."}}}
    m["content_hash"] = content_hash(m)
    errs = [e.message for e in validator("ReleaseManifest").iter_errors(m)]
    if errs:
        raise SystemExit(f"ReleaseManifest 검증 실패: {errs[:3]}")
    (out / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (Path(a.out) / "PREPARE_REPORT.json").write_text(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def file_hash_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--adapter-a", required=True, help="LoRA-A 어댑터 디렉터리(adapter_config.json, adapter_model.safetensors)")
    ap.add_argument("--adapter-b", required=True)
    ap.add_argument("--c1-a", required=True, help="C1 lora_a jsonl(첫 행의 system 메시지를 A의 시스템 프롬프트로 쓴다)")
    ap.add_argument("--c1-b", required=True)
    ap.add_argument("--out", required=True, help="출력 디렉터리(서버에서는 ~/dabin 안)")
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "gpu-shared-8b.json"))
    ap.add_argument("--backend", choices=("vllm", "fake"), default="vllm")
    ap.add_argument("--vllm-version", help="서버의 vllm 버전(예: 0.31.0)")
    ap.add_argument("--release-id", default="gpu-session")
    ap.add_argument("--max-tokens-a", type=int, default=512, help="기본 512는 neutral 패키지의 값이다. 측정 근거 없음")
    ap.add_argument("--max-tokens-b", type=int, default=256, help="기본 256은 B 출력(1~3문장)에 넉넉하다는 추정이다. 측정 근거 없음")
    ap.add_argument("--rewrite-base-name", action="store_true", help="adapter_config의 base_model_name_or_path가 다를 때 복사본의 값을 프로파일 기반 모델로 바꾼다")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--turn-engine-version", default=os.environ.get("MODEL_RUNTIME_TURN_ENGINE_MARKER", mr.TURN_ENGINE_MARKER_VERSION), help="매니페스트 turn_engine_version 표지(D109, 기본은 라이브러리의 고정 표지)")
    a = ap.parse_args()
    rep = build(a)
    print(json.dumps({"release": str(Path(a.out) / a.release_id), "warnings": rep["warnings"],
                      "roles": {r: {k: v for k, v in x.items() if k in ("adapter_hash", "system_prompt_kinds", "system_prompt_chars")} | {"base": x["adapter"]["base_model_name_or_path"], "r": x["adapter"]["r"]}
                                for r, x in rep["roles"].items()}}, ensure_ascii=False, indent=1))
    for w in rep["warnings"]:
        print("경고:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
