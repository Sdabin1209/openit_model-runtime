"""입출력 검증, 문진 스키마 정수 버전 확인, 제약용 스키마 완화.

2.0.0(D74): 모델 입출력 스키마(`ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`)는
boundary-contracts 패키지에서 가져온다(`load_schema`, `validator`, 복사본 없음). 문진 스키마 배포 디렉터리(`schema_dir`)는
`runtime_contract_version`(문진 스키마 정수 버전, D75)을 확인하는 데만 쓴다."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable

import jsonschema
from boundary_contracts import load_schema, validator

from .errors import ContractVersionMismatch, ContractViolation

SUPPORTED_RUNTIME_CONTRACT_VERSION = 1
RELAX_POLICY_VERSION = "relax-1"

INPUT_CONTRACT = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
OUTPUT_CONTRACT = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}
JSON_OUTPUT_ROLES = ("LORA_A",)   # JSON을 내는 역할. LORA_B는 문장 그대로의 텍스트다(제약 디코딩 없음)


def violations_of(errors: Iterable[jsonschema.ValidationError]) -> list[tuple[str, str]]:
    out = []
    for e in errors:
        path = "/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else ""
        out.append((path, str(e.validator)))
    return sorted(set(out))


def _strip_comments(node: Any) -> Any:
    """제약 디코딩 엔진에 넘기는 스키마에서 설명용 `$comment`를 뺀다(검증에는 영향이 없다)."""
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if k != "$comment"}
    if isinstance(node, list):
        return [_strip_comments(v) for v in node]
    return node


class SchemaSet:
    def __init__(self, schema_dir: Path | str):
        """schema_dir: 문진 스키마의 배포 스키마 디렉터리(`<...>/schema/schemas`). CONTRACT_VERSION은 그 상위에서 읽는다.
        환경 변수와 전역 설정은 읽지 않는다. 모델 입출력 스키마는 이 디렉터리가 아니라 boundary_contracts에서 읽는다."""
        d = Path(schema_dir)
        try:
            self.contract_version = int((d.parent / "CONTRACT_VERSION").read_text().strip())
        except (OSError, ValueError):
            raise ContractVersionMismatch("문진 스키마 CONTRACT_VERSION을 읽을 수 없다") from None
        if self.contract_version != SUPPORTED_RUNTIME_CONTRACT_VERSION:
            raise ContractVersionMismatch(
                f"문진 스키마 버전 {self.contract_version}은 지원하지 않는다 (지원: {SUPPORTED_RUNTIME_CONTRACT_VERSION})"
            )
        self._validators = {name: validator(name) for name in [*INPUT_CONTRACT.values(), *OUTPUT_CONTRACT.values()]}
        self._model_schemas = {role: _strip_comments(load_schema(OUTPUT_CONTRACT[role])) for role in JSON_OUTPUT_ROLES}

    def model_output_schema(self, role: str) -> dict | None:
        """제약 디코딩에 쓸 출력 스키마(완화 전). 텍스트를 내는 역할(LORA_B)은 None."""
        return copy.deepcopy(self._model_schemas[role]) if role in self._model_schemas else None

    def validate_input(self, role: str, value: Any) -> None:
        name = INPUT_CONTRACT[role]
        errs = list(self._validators[name].iter_errors(value))
        if errs:
            raise ContractViolation(f"{name} 입력 계약 위반", violations_of(errs))

    def output_violations(self, role: str, value: Any) -> list[tuple[str, str]]:
        """출력 계약(ActionExtraction 또는 ResponseText)으로 엄격하게 검증한다. 위반이 없으면 빈 목록."""
        return violations_of(self._validators[OUTPUT_CONTRACT[role]].iter_errors(value))


# --- 완화 -----------------------------------------------------------------

_SCHEMA_MAPS = ("properties", "$defs", "patternProperties", "dependentSchemas")
_SCHEMA_SINGLE = ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames")
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NON_SCHEMA = ("enum", "const", "required", "type", "default", "examples")


def relax(schema: dict, supported_features: Iterable[str]) -> tuple[dict, list[str]]:
    """엔진이 지원하지 않는 키워드를 허용 범위를 넓히는 방향으로만 바꾼다.

    - 지원하지 않는 oneOf: 가지가 모두 const면 enum으로(같은 뜻), 아니면 anyOf로(넓힘), anyOf도 없으면 뺀다.
    - 그 밖의 지원하지 않는 키워드는 뺀다. if/then/else는 함께 다룬다.
    순수 함수다. 돌려주는 목록은 바뀐 키워드(정렬, 중복 없음)다."""
    feats = frozenset(supported_features)
    changed: set[str] = set()

    def node(s: Any) -> Any:
        if not isinstance(s, dict):
            return s
        out: dict[str, Any] = {}
        for k, v in s.items():
            if k in _NON_SCHEMA:
                if k in ("enum", "const", "required", "type") and k not in feats and k != "type":
                    changed.add(k)
                    continue
                out[k] = v
            elif k == "oneOf" and "oneOf" not in feats:
                if all(isinstance(b, dict) and set(b) == {"const"} for b in v) and "enum" in feats:
                    out["enum"] = [b["const"] for b in v]
                    changed.add("oneOf->enum")
                elif "anyOf" in feats:
                    out["anyOf"] = [node(b) for b in v]
                    changed.add("oneOf->anyOf")
                else:
                    changed.add("oneOf")
            elif k in ("if", "then", "else") and "if" not in feats:
                changed.add(k)
            elif k in _SCHEMA_LISTS:
                if k not in feats and k != "oneOf":
                    changed.add(k)
                else:
                    out[k] = [node(b) for b in v]
            elif k in _SCHEMA_MAPS:
                out[k] = {n: node(b) for n, b in v.items()}
            elif k in _SCHEMA_SINGLE:
                if k not in feats:
                    changed.add(k)
                else:
                    out[k] = node(v) if isinstance(v, dict) else v
            else:
                if k.startswith("$") or k in feats or k in ("title", "description"):
                    out[k] = v
                else:
                    changed.add(k)
        return out

    return node(schema), sorted(changed)
