#!/usr/bin/env python3
"""서빙 렌더러(prompt-4)가 학습 데이터의 user 메시지와 글자 단위로 같은지 확인한다 (D77).

사용: <jsonl을 표준출력으로 내는 명령> | python scripts/check_prompt_matches_dataset.py [--role auto|LORA_A|LORA_B] [--shuffle]
입력(stdin): 한 줄에 {"messages": [{"role":"system"|"user"|"assistant","content":...}, ...]} 인 jsonl(학습 데이터 형식).
각 행에서 user 메시지를 JSON으로 읽어 입력 객체를 복원하고(키 순서는 읽은 그대로), 렌더러가 만든 user 메시지와 전체 프롬프트가
원본(학습 글자)과 같은지 비교한다. --shuffle은 최상위 키(A) 또는 모든 수준의 키(B) 순서를 섞어 넣어도 같은 결과인지 본다
(A의 state 내부 순서는 호출자 책임이라 섞지 않는다). 데이터는 저장하지 않고 일치 개수와 어긋난 행의 번호·위치만 낸다(본문은 내지 않는다).
종료 코드: 모두 일치 0, 어긋남 있음 1, 읽을 행이 없음 2."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import model_runtime as mr  # noqa: E402
from model_runtime import prompts  # noqa: E402

SCHEMA_DIR = ROOT / "tests" / "fixtures" / "schema" / "schemas"


def detect_role(obj: dict) -> str:
    return "LORA_A" if "question_id" in obj and "user_utterance" in obj and "state" in obj else "LORA_B"


def shuffled(obj, role: str, rnd: random.Random, top: bool = True):
    if isinstance(obj, dict):
        keys = list(obj)
        if role == "LORA_B" or top:
            rnd.shuffle(keys)
        deeper = role == "LORA_B"
        return {k: shuffled(obj[k], role, rnd, False) if deeper else obj[k] for k in keys}
    if isinstance(obj, list) and role == "LORA_B":
        return [shuffled(x, role, rnd, False) for x in obj]
    return obj


def first_diff(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def check_row(row: dict, role_opt: str, shuffle: bool, rnd: random.Random):
    """(role, 문제 목록). 문제는 'user'(직렬화 불일치), 'prompt'(전체 프롬프트 불일치), 'schema'(스키마 위반; 직렬화 비교는 계속)."""
    msgs = {m["role"]: m["content"] for m in row["messages"]}
    user, system = msgs["user"], msgs["system"]
    obj = json.loads(user)
    role = role_opt if role_opt != "auto" else detect_role(obj)
    probe = shuffled(obj, role, rnd) if shuffle else obj
    problems = []
    got_user = prompts._USER["prompt-4"](role, probe)
    if got_user != user:
        problems.append(("user", first_diff(got_user, user)))
    expect = prompts._qwen3_nothink(system, user)
    try:
        got = mr.render_prompt(role, probe, serving_config={"prompt_template_version": "prompt-4"},
                               system_prompt=system, schema_dir=SCHEMA_DIR)
        if got != expect:
            problems.append(("prompt", first_diff(got, expect)))
    except mr.ContractViolation:
        problems.append(("schema", -1))
    return role, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--role", default="auto", choices=["auto", "LORA_A", "LORA_B"])
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    rnd = random.Random(args.seed)
    n = 0
    stats: dict[str, dict[str, int]] = {}
    bad: list[str] = []
    for i, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        n += 1
        role, problems = check_row(json.loads(line), args.role, args.shuffle, rnd)
        st = stats.setdefault(role, {"rows": 0, "user_match": 0, "prompt_match": 0, "schema_invalid": 0})
        st["rows"] += 1
        kinds = {k for k, _ in problems}
        st["user_match"] += "user" not in kinds
        st["prompt_match"] += not (kinds & {"user", "prompt"}) and "schema" not in kinds
        st["schema_invalid"] += "schema" in kinds
        for k, pos in problems:
            bad.append(f"행 {i} {role} {k}" + (f" 첫 어긋남 위치 {pos}" if pos >= 0 else ""))
    if n == 0:
        print("읽을 행이 없다", file=sys.stderr)
        return 2
    for role, st in sorted(stats.items()):
        print(f"{role}: 행 {st['rows']}, user 글자 일치 {st['user_match']}, 전체 프롬프트 일치 {st['prompt_match']}, 스키마 위반 {st['schema_invalid']}"
              f"{' (키 순서 섞음)' if args.shuffle else ''}")
    for b in bad[:20]:
        print("  어긋남:", b)
    if len(bad) > 20:
        print(f"  ... 외 {len(bad) - 20}건")
    ok = not bad
    print("결과:", "모두 일치" if ok else "불일치 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
