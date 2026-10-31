"""serving_config_check 컴포넌트: ServingConfig 형식 검증, 지원 여부, 해시 (INTERFACES I9). ServingConfigCheckInput -> ServingConfigCheckResult.

형식의 정본은 계약 ServingConfig(`validate_serving_config`). 이 영역이 지원하는 값(템플릿·계약·보호·완화 버전)은 입력 스키마가 정한다.
스키마로 쓸 수 없는 조건(schemas/README.md의 표): 설정의 constraint가 사용할 백엔드와 같은가, 해시가 기대 해시와 같은가."""
from __future__ import annotations

from typing import Mapping

from boundary_contracts import validator
from boundary_contracts.canonical import sha256_hash
from ..wiring.errors import ContractViolation, ServingConfigMismatch, UnsupportedServingConfig

_VALIDATOR = validator("ServingConfig")   # 형식의 정본은 계약 스키마다(영역 안에 사본을 두지 않는다, U44). 해시는 받은 객체 그대로라 모르는 키는 거부된다
INPUT, OUTPUT = "ServingConfigCheckInput", "ServingConfigCheckResult"


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


def run(req: Mapping) -> dict:
    """백엔드 일치(스키마 밖)와 기대 해시 일치(스키마 밖)를 보고 해시를 낸다. 설정 형식은 호출 전에 validate_serving_config로 본다."""
    role, cfg, b = req["role"], req["serving_config"], req["backend"]
    c = cfg["constraint"]
    if (c["backend"], c["backend_version"]) != (b["name"], b["version"]) or frozenset(c["supported_features"]) != frozenset(b["supported_features"]):
        raise UnsupportedServingConfig(f"{role}: serving_config.constraint가 사용할 백엔드와 다르다")
    h = serving_config_hash(cfg)
    if h != req["expected_hash"]:
        raise ServingConfigMismatch(role)
    return {"serving_config_hash": h}
