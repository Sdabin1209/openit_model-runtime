"""io_validate 컴포넌트: 역할 한 개의 입력 또는 출력을 계약 스키마로 검증한다. IoValidationInput -> IoValidationResult.

2.0.0(D74): 모델 입출력 스키마(`ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`)는 boundary-contracts 패키지에서 가져온다
(`validator`, 복사본 없음). 검증기는 `pattern`의 `$`를 `\\Z`로 읽는다(MR-14).
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
_END_ANCHOR = re.compile(r"(?<!\\)\$")


def _strict_pattern_validator(v):
    """`pattern`의 `$`는 파이썬 `re`에서 끝 줄바꿈 앞에도 맞는다(`Q4.answer\\n`이 통과, MR-14). 검증기에서만 `$`를 `\\Z`로 읽는다.
    계약 스키마 원본은 바꾸지 않는다(제약 디코딩에 주는 스키마도 그대로)."""
    def pattern(validator_, patrn, instance, schema):
        if validator_.is_type(instance, "string") and not re.search(_END_ANCHOR.sub(r"\\Z", patrn), instance):
            yield jsonschema.ValidationError(f"does not match {patrn!r}")
    cls = jsonschema.validators.extend(type(v), {"pattern": pattern})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return cls(v.schema, resolver=v.resolver)


_VALIDATORS: dict = {}


def _v(name: str):
    if name not in _VALIDATORS:
        _VALIDATORS[name] = _strict_pattern_validator(validator(name))
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
