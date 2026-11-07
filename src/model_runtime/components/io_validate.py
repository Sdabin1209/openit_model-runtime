"""io_validate 컴포넌트: 역할 한 개의 입력 또는 출력을 계약 스키마로 검증한다. IoValidationInput -> IoValidationResult.

2.0.0(D74): 모델 입출력 스키마(`ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`)는 boundary-contracts 패키지에서 가져온다
(`validator`, 복사본 없음). 끝 개행은 계약 패턴이 직접 거부한다(4.0.0, MR-14).
`output_schema`는 제약 디코딩에 쓸 출력 스키마(완화 전)를 낸다. 제약을 쓸지는 역할이 정한다(MR-01)."""
from __future__ import annotations

import re
import warnings
from typing import Any, Mapping

import jsonschema
from boundary_contracts import load_schema, validator

INPUT, OUTPUT = "IoValidationInput", "IoValidationResult"
INPUT_CONTRACT = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
OUTPUT_CONTRACT = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}


_VALIDATORS: dict = {}


def _v(name: str):
    if name not in _VALIDATORS:
        _VALIDATORS[name] = validator(name)   # 계약 4.0.0 부터 pattern 끝 고정이 $(?![\s\S]) 라 끝 개행을 계약이 직접 거부한다(MR-14 의 \Z 우회 검사기는 없앴다)
    return _VALIDATORS[name]


def violations_of(errors) -> list[tuple[str, str]]:
    out = []
    for e in errors:
        path = "/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else ""
        out.append((path, str(e.validator)))
    return sorted(set(out))


def run(req: Mapping) -> dict:
    name = (INPUT_CONTRACT if req["direction"] == "input" else OUTPUT_CONTRACT)[req["role"]]
    return {"violations": [{"path": p, "keyword": k} for p, k in violations_of(_v(name).iter_errors(req["value"]))]}


def output_schema(role: str) -> dict:
    """제약 디코딩에 쓸 출력 스키마(완화 전). 설명용 `$comment`는 뺀다(검증에는 영향이 없다)."""
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "$comment"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(load_schema(OUTPUT_CONTRACT[role]))
