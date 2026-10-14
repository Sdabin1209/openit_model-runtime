"""문진 스키마 로딩, 입출력 검증, 제약용 스키마 완화."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from .errors import ContractVersionMismatch, ContractViolation

SUPPORTED_RUNTIME_CONTRACT_VERSION = 1
RELAX_POLICY_VERSION = "relax-1"

INPUT_CONTRACT = {"LORA_A": "UtteranceParserInput", "LORA_B": "ResponseGeneratorInput"}
OUTPUT_CONTRACT = {"LORA_A": "ParserOutput", "LORA_B": "GeneratorOutput"}
# 모델이 내는 것. 생성기는 response_text만 내고 is_fallback은 코드가 채운다(D36).
MODEL_ONLY_REMOVE = {"LORA_B": ("is_fallback",)}


def violations_of(errors: Iterable[jsonschema.ValidationError]) -> list[tuple[str, str]]:
    out = []
    for e in errors:
        path = "/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else ""
        out.append((path, str(e.validator)))
    return sorted(set(out))


class SchemaSet:
    def __init__(self, schema_dir: Path | str):
        """schema_dir: 문진 스키마의 배포 스키마 디렉터리(`<...>/schema/schemas`, <계약명>.json이 있는 곳).
        CONTRACT_VERSION은 그 상위에서 읽는다. 환경 변수와 전역 설정은 읽지 않는다."""
        d = Path(schema_dir)
        try:
            self.contract_version = int((d.parent / "CONTRACT_VERSION").read_text().strip())
        except (OSError, ValueError):
            raise ContractVersionMismatch("문진 스키마 CONTRACT_VERSION을 읽을 수 없다") from None
        if self.contract_version != SUPPORTED_RUNTIME_CONTRACT_VERSION:
            raise ContractVersionMismatch(
                f"문진 스키마 버전 {self.contract_version}은 지원하지 않는다 (지원: {SUPPORTED_RUNTIME_CONTRACT_VERSION})"
            )
        self._schemas: dict[str, dict] = {}
        self._validators: dict[str, jsonschema.Draft202012Validator] = {}
        for name in [*INPUT_CONTRACT.values(), *OUTPUT_CONTRACT.values()]:
            schema = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
            # 상대 $id가 있으면 '#/$defs/..' 참조가 원격 주소로 풀리려 한다. 메모리 사본에서만 뺀다.
            schema.pop("$id", None)
            jsonschema.Draft202012Validator.check_schema(schema)
            self._schemas[name] = schema
            self._validators[name] = jsonschema.Draft202012Validator(schema)
        self._model_schemas: dict[str, dict] = {}
        self._model_validators: dict[str, jsonschema.Draft202012Validator] = {}
        for role, name in OUTPUT_CONTRACT.items():
            ms = _drop_fields(self._schemas[name], name, MODEL_ONLY_REMOVE.get(role, ()))
            self._model_schemas[role] = ms
            self._model_validators[role] = jsonschema.Draft202012Validator(ms)

    def schema(self, name: str) -> dict:
        return self._schemas[name]

    def model_output_schema(self, role: str) -> dict:
        """모델이 내야 하는 모양의 스키마(제약 디코딩과 사후 검증에 쓴다)."""
        return self._model_schemas[role]

    def vocabulary(self, name: str, def_name: str) -> list[str]:
        """$defs의 열거형(oneOf const 또는 enum) 값 목록. 스키마 순서."""
        node = self._schemas[name]["$defs"][def_name]
        if "enum" in node:
            return list(node["enum"])
        return [b["const"] for b in node["oneOf"]]

    def validate_input(self, role: str, value: Any) -> None:
        name = INPUT_CONTRACT[role]
        errs = list(self._validators[name].iter_errors(value))
        if errs:
            raise ContractViolation(f"{name} 입력 계약 위반", violations_of(errs))

    def output_violations(self, role: str, value: Any) -> list[tuple[str, str]]:
        """원래(완화하지 않은) 출력 스키마로 검증한다. 위반이 없으면 빈 목록."""
        return violations_of(self._validators[OUTPUT_CONTRACT[role]].iter_errors(value))

    def model_output_violations(self, role: str, value: Any) -> list[tuple[str, str]]:
        """모델이 내는 모양(생성기는 response_text만)으로 검증한다."""
        return violations_of(self._model_validators[role].iter_errors(value))


def _drop_fields(schema: dict, def_name: str, fields: tuple) -> dict:
    out = copy.deepcopy(schema)
    if fields:
        node = out["$defs"][def_name]
        for f in fields:
            node.get("properties", {}).pop(f, None)
            if "required" in node:
                node["required"] = [r for r in node["required"] if r != f]
    return out


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
