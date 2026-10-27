#!/usr/bin/env python3
"""D83: relax-3 변환 스키마를 xgrammar로 컴파일해 수락·거부를 본다(CPU, GPU·모델 불필요).

사용: <xgrammar가 깔린 파이썬> scripts/xgrammar_check.py [--original]
필요: xgrammar(vLLM 0.31.0이 쓰는 0.2.7 근처), boundary-contracts, jsonschema, 이 리포(src/). 토크나이저 대신 한 글자 토큰 어휘를 쓴다.
기준 판정은 원본 계약 스키마(jsonschema)다. 문자열은 스키마 속성 순서(xgrammar는 순서를 강제한다)로 직렬화한다.
--original: 변환 없이 원본 스키마를 컴파일한다(allOf 경고·수락 비교용)."""
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
import jsonschema
import xgrammar as xg
from boundary_contracts import load_schema

import model_runtime as mr
from model_runtime.schema_guard import relax
import test_anyof_constraint as T

orig = T.strip(load_schema("ActionExtraction"))
schema = orig if "--original" in sys.argv else relax(orig, set(mr.DEFAULT_SUPPORTED_FEATURES))[0]
truth = jsonschema.Draft202012Validator(load_schema("ActionExtraction"))

vocab = [chr(i) for i in range(32, 127)] + ["</s>"]
stop = len(vocab) - 1
ti = xg.TokenizerInfo(vocab, stop_token_ids=[stop])
compiled = xg.GrammarCompiler(ti).compile_json_schema(json.dumps(schema))

TOP = list(orig["properties"])
ITEM = list(orig["properties"]["values"]["items"]["properties"])


def ordered(d):
    d = dict(d)
    if isinstance(d.get("values"), list):
        d["values"] = [{k: it[k] for k in ITEM if k in it} | {k: v for k, v in it.items() if k not in ITEM} if isinstance(it, dict) else it
                       for it in d["values"]]
    return {k: d[k] for k in TOP if k in d} | {k: v for k, v in d.items() if k not in TOP}


def accepts(text):
    m = xg.GrammarMatcher(compiled)
    return bool(m.accept_string(text)) and bool(m.accept_token(stop))


corpus = [(f"top{i}", d) for i, d in enumerate(T.top_cases())] + [(f"item{i}", d) for i, d in enumerate(T.item_cases())] \
    + T.mutations() + list(T.examples().items())
stats = {"valid_accepted": 0, "valid_rejected": 0, "invalid_rejected": 0, "invalid_accepted": 0}
bad = []
for name, d in corpus:
    v = truth.is_valid(d)
    a = accepts(json.dumps(ordered(d), ensure_ascii=False))
    k = ("valid_" if v else "invalid_") + ("accepted" if a else "rejected")
    stats[k] += 1
    if v != a:
        bad.append((name, v, a))
print("schema:", "original" if "--original" in sys.argv else "relax-3", "cases:", len(corpus))
print(stats)
print("mismatch:", len(bad))
for b in bad[:40]:
    print("  ", b)


# --- 키 순서(계약 v2.0.1: properties는 사전순이고 순서의 정본은 계약이다. relax는 순서를 건드리지 않는다) ---
def reverse_keys(d):
    """같은 내용을 키 순서만 거꾸로(최상위와 values 항목 모두) 직렬화한다."""
    d = dict(reversed(list(d.items())))
    if isinstance(d.get("values"), list):
        d["values"] = [dict(reversed(list(it.items()))) for it in d["values"]]
    return d


assert TOP == sorted(TOP) and ITEM == sorted(ITEM), "계약 properties가 사전순이 아니다(v2.0.1 이상 필요)"
ex = T.examples()
srt = sum(accepts(json.dumps(dict(sorted(d.items())) | {"values": [dict(sorted(i.items())) for i in d["values"]]}, ensure_ascii=False))
          for d in ex.values())
rev = sum(not accepts(json.dumps(reverse_keys(d), ensure_ascii=False)) for d in ex.values())
valid_cases = [d for _, d in corpus if truth.is_valid(d)]
rev_all = sum(not accepts(json.dumps(reverse_keys(d), ensure_ascii=False)) for d in valid_cases)
print(f"key order: sorted examples accepted {srt}/{len(ex)}, reversed examples rejected {rev}/{len(ex)}, "
      f"reversed valid cases rejected {rev_all}/{len(valid_cases)}")
