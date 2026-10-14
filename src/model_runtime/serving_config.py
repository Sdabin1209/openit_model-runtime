"""ServingConfig 형식, 검증, 해시 (INTERFACES I9)."""
from __future__ import annotations

from typing import Any, Mapping

import jsonschema

from boundary_contracts.canonical import sha256_hash
from .errors import ContractViolation

SERVING_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt_template_version", "decoding", "max_attempts", "constraint", "output_contract"],
    "properties": {
        "prompt_template_version": {"type": "string", "minLength": 1},
        "decoding": {
            "type": "object",
            "additionalProperties": False,
            "required": ["constraint_mode", "temperature", "seed", "max_tokens"],
            "properties": {
                "constraint_mode": {"enum": ["constrained", "none"]},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "seed": {"type": "integer", "minimum": 0, "maximum": 2**31 - 1},
                "max_tokens": {"type": "integer", "minimum": 1},
                "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            },
        },
        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 5},
        "constraint": {
            "type": "object",
            "additionalProperties": False,
            "required": ["backend", "backend_version", "supported_features", "relax_policy_version"],
            "properties": {
                "backend": {"type": "string", "minLength": 1},
                "backend_version": {"type": "string", "minLength": 1},
                "supported_features": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "relax_policy_version": {"type": "string", "minLength": 1},
            },
        },
        "output_contract": {"type": "integer", "minimum": 1},
    },
}

_VALIDATOR = jsonschema.Draft202012Validator(SERVING_CONFIG_SCHEMA)
DEFAULT_MAX_ATTEMPTS = 3


def validate_serving_config(serving_config: Mapping) -> None:
    """형식 위반이면 ContractViolation. 값은 예외에 담지 않는다."""
    if not isinstance(serving_config, Mapping):
        raise ContractViolation("serving_config는 객체여야 한다")
    cfg = dict(serving_config)
    errs = sorted(_VALIDATOR.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        raise ContractViolation(
            "serving_config 형식 위반",
            [("/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else "", e.validator) for e in errs],
        )
    try:
        sha256_hash(cfg)
    except ValueError as e:
        raise ContractViolation(f"serving_config를 정규화할 수 없다: {e}") from None


def serving_config_hash(serving_config: Mapping) -> str:
    """받은 설정 그대로(기본값을 채우지 않고) JCS 해시한다."""
    validate_serving_config(serving_config)
    return sha256_hash(dict(serving_config))
