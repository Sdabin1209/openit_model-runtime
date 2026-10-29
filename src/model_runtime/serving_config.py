"""ServingConfig 형식, 검증, 해시 (INTERFACES I9)."""
from __future__ import annotations

from typing import Mapping

from boundary_contracts import validator
from boundary_contracts.canonical import sha256_hash
from .errors import ContractViolation

_VALIDATOR = validator("ServingConfig")   # 형식의 정본은 계약 스키마다(영역 안에 사본을 두지 않는다, U44). 해시는 받은 객체 그대로라 모르는 키는 거부된다
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
