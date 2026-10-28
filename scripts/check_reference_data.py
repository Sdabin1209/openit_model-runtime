#!/usr/bin/env python3
"""최종 학습 데이터(C1) 전체와 서빙(v0.7.3 시점)의 대조 보고. 데이터는 읽기 전용이고 리포에 복사하지 않는다. 본문·발화는 출력하지 않는다(건수·길이·행 번호·해시만).

사용: MODEL_RUNTIME_REFERENCE_DATA_DIR=<.../data/final> python scripts/check_reference_data.py [--splits val,test,train] [--limit N] [--json]
          [--system-prompt-a FILE --system-prompt-b FILE]   (서빙 번들의 시스템 프롬프트와 해시 비교, 선택)
디렉터리에 lora_{a,b}_{train,val,test}.jsonl이 있어야 한다. 환경 변수가 없거나 파일이 없으면 종료 코드 0으로 '건너뜀'을 알린다(선택 검사).
대조 항목: (1) render_prompt(prompt-4)가 각 행의 system+user를 글자 단위로 재현하는 비율 (2) 시스템 프롬프트 종류와 번들 일치
(3) assistant 정답이 사후 검증기(엄격)와 제약 디코딩용 완화 스키마(relax-3)를 통과하는 비율, 키 순서 (4) user 메시지 길이 분포와 MAX_INPUT_CHARS."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import jsonschema  # noqa: E402
from boundary_contracts import load_schema  # noqa: E402

import model_runtime as mr  # noqa: E402
from model_runtime import prompts, server  # noqa: E402
from model_runtime.schema_guard import SchemaSet, relax  # noqa: E402

SCHEMA_DIR = ROOT / "tests" / "fixtures" / "schema" / "schemas"
ROLE_OF = {"a": "LORA_A", "b": "LORA_B"}
FEATURES = sorted(mr.FakeBackend().supported_features | {"pattern", "minItems", "maxItems"})   # vLLM/xgrammar relax-3 지원 집합과 같게(serving_config.constraint)


def sha(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))] if xs else None


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def run(data_dir: Path, splits, limit, sys_prompts):
    schemas = SchemaSet(SCHEMA_DIR)
    relaxed = {}
    for role in ("LORA_A",):
        rs, _ = relax(schemas.model_output_schema(role), FEATURES)
        relaxed[role] = jsonschema.Draft202012Validator(rs)
    prop_order = list(load_schema("ActionExtraction")["properties"])
    out = {}
    for r in ("a", "b"):
        role = ROLE_OF[r]
        S = collections.Counter(); sysk = collections.Counter(); lens = []; len_by_split = collections.defaultdict(list)
        causes = collections.Counter(); asst = collections.Counter(); samples = collections.defaultdict(list)
        over = 0; over_rows = []; neutral_changed = 0
        for sp in splits:
            f = data_dir / f"lora_{r}_{sp}.jsonl"
            if not f.exists():
                continue
            with open(f, encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    if limit and i >= limit:
                        break
                    row = json.loads(line)
                    m = {x["role"]: x["content"] for x in row["messages"]}
                    system, user, ans = m["system"], m["user"], m["assistant"]
                    S["rows"] += 1; S[f"rows_{sp}"] += 1
                    sysk[sha(system)] += 1
                    # (1) 렌더링 재현
                    obj = None
                    try:
                        obj = json.loads(user)
                        exp = prompts._qwen3_nothink(system, user)
                        got = mr.render_prompt(role, obj, serving_config={"prompt_template_version": "prompt-4"}, system_prompt=system, schema_dir=SCHEMA_DIR)
                        if got == exp:
                            S["render_match"] += 1
                        else:
                            gu = prompts._USER["prompt-4"](role, obj)
                            c = "user_serialization" if gu != user else "template"
                            causes[c] += 1; samples[c].append((sp, i, first_diff(got, exp)))
                    except mr.ContractViolation as e:
                        causes["input_schema_violation"] += 1
                        samples["input_schema_violation"].append((sp, i, str(getattr(e, "errors", ""))[:120]))
                    except ValueError:
                        causes["user_not_json"] += 1; samples["user_not_json"].append((sp, i, -1))
                    # (4) 길이: 서버가 재는 방식(JSON 직렬화 문자 수)과 원문 문자 수
                    try:
                        n = len(json.dumps(obj, ensure_ascii=False))
                    except Exception:
                        n = len(user)
                    lens.append(n); len_by_split[sp].append(n)
                    if obj is not None and prompts.neutralize_special_tokens(obj) != obj:   # 특수 토큰 방어(MR-2)가 학습 분포의 행을 바꾸는가
                        neutral_changed += 1
                    if n > server.MAX_INPUT_CHARS:
                        over += 1; over_rows.append((sp, i, n))
                    # (3) 정답 검증
                    try:
                        val = json.loads(ans) if role == "LORA_A" else ans.strip()
                    except ValueError:
                        asst["not_json"] += 1; samples["asst_not_json"].append((sp, i)); continue
                    viol = schemas.output_violations(role, val)
                    if viol:
                        asst["strict_reject"] += 1
                        for v in set(viol):
                            asst["strict_reject:" + v[0].rsplit("/", 1)[-1] + ":" + v[1] if v[0] else "strict_reject:root:" + v[1]] += 1
                        samples["asst_strict_reject"].append((sp, i, sorted(set(viol))[:3]))
                    else:
                        asst["strict_ok"] += 1
                    if role == "LORA_A":
                        if relaxed[role].is_valid(val):
                            asst["relaxed_ok"] += 1
                        else:
                            asst["relaxed_reject"] += 1; samples["asst_relaxed_reject"].append((sp, i))
                        keys = list(val) if isinstance(val, dict) else []
                        if keys == [k for k in prop_order if k in keys]:
                            asst["key_order_ok"] += 1
                        else:
                            asst["key_order_not_schema_order"] += 1
        out[role] = {
            "rows": S["rows"], "rows_by_split": {k[5:]: v for k, v in S.items() if k.startswith("rows_")},
            "render_match": S["render_match"], "render_mismatch_causes": dict(causes),
            "system_prompt_kinds": {k: v for k, v in sysk.items()},
            "assistant": dict(asst),
            "user_len": {"n": len(lens), "max": max(lens) if lens else None, "p50": pct(lens, 50), "p99": pct(lens, 99), "p999": pct(lens, 99.9),
                         "by_split_max": {k: max(v) for k, v in len_by_split.items()}},
            "over_max_input_chars": over, "rows_changed_by_special_token_neutralization": neutral_changed, "max_input_chars": server.MAX_INPUT_CHARS, "over_rows_head": over_rows[:10],
            "samples": {k: v[:5] for k, v in samples.items()},
        }
        if sys_prompts.get(r):
            out[role]["bundle_system_prompt_match"] = {k: (k == sha(sys_prompts[r])) for k in sysk}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--splits", default="val,test,train")
    ap.add_argument("--limit", type=int, default=0, help="분할마다 앞 N행만(0이면 전체)")
    ap.add_argument("--system-prompt-a"); ap.add_argument("--system-prompt-b")
    a = ap.parse_args(argv)
    d = os.environ.get("MODEL_RUNTIME_REFERENCE_DATA_DIR")
    if not d or not Path(d).is_dir() or not list(Path(d).glob("lora_a_*.jsonl")):
        print("건너뜀: MODEL_RUNTIME_REFERENCE_DATA_DIR에 lora_{a,b}_{train,val,test}.jsonl이 없다")
        return 0
    sp = {r: Path(p).read_text(encoding="utf-8") for r, p in (("a", a.system_prompt_a), ("b", a.system_prompt_b)) if p}
    print(json.dumps(run(Path(d), a.splits.split(","), a.limit, sp), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
