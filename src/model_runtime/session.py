"""ModelSession: 렌더 → 생성 → 사후 검증 → 재시도. FakeModelSession도 같은 코드를 쓴다."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .backends import ConstraintBackend, FakeBackend, GenerationRequest
from .errors import (BackendNotAvailable, ContractViolation, ModelOutputInvalid, ServingConfigMismatch,
                     UnsupportedServingConfig)
from .prompts import SUPPORTED_TEMPLATE_VERSIONS, render_prompt
from .schema_guard import (INPUT_CONTRACT, OUTPUT_CONTRACT, RELAX_POLICY_VERSION,
                           SUPPORTED_RUNTIME_CONTRACT_VERSION, SchemaSet, relax)
from .serving_config import serving_config_hash, validate_serving_config

RUNTIME_VERSION = "0.1.0"
log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.


@dataclass(frozen=True)
class CallInfo:
    """마지막 호출의 메타데이터. 원문을 담지 않는다."""
    role: str
    attempts: int
    relaxed: tuple


class ModelSession(Protocol):
    serving_config_hash: str
    last_call_info: CallInfo | None

    def parse(self, input: Mapping) -> dict: ...
    def generate(self, input: Mapping) -> dict: ...


def _check_config(cfg: Mapping, backend: ConstraintBackend) -> None:
    validate_serving_config(cfg)
    if cfg["output_contract"] != SUPPORTED_RUNTIME_CONTRACT_VERSION:
        raise UnsupportedServingConfig("지원하지 않는 output_contract")
    if cfg["prompt_template_version"] not in SUPPORTED_TEMPLATE_VERSIONS:
        raise UnsupportedServingConfig("지원하지 않는 프롬프트 템플릿 버전")
    c = cfg["constraint"]
    if c["relax_policy_version"] != RELAX_POLICY_VERSION:
        raise UnsupportedServingConfig("지원하지 않는 완화 정책 버전")
    if (c["backend"], c["backend_version"]) != (backend.name, backend.version) or \
            frozenset(c["supported_features"]) != frozenset(backend.supported_features):
        raise UnsupportedServingConfig("serving_config.constraint가 사용할 백엔드와 다르다")


class _Session:
    def __init__(self, cfg: Mapping, backend: ConstraintBackend, schemas: SchemaSet, cfg_hash: str):
        self._cfg = json.loads(json.dumps(cfg))
        self._backend = backend
        self._schemas = schemas
        self.serving_config_hash = cfg_hash
        self.last_call_info: CallInfo | None = None
        self._constraint: dict[str, tuple[dict, tuple]] = {}
        for role, name in OUTPUT_CONTRACT.items():
            relaxed, removed = relax(schemas.schema(name), backend.supported_features)
            self._constraint[role] = (relaxed, tuple(removed))

    def _run(self, role: str, input: Mapping) -> dict:
        cfg = self._cfg
        prompt = render_prompt(role, input, serving_config=cfg, schemas=self._schemas)
        dec = cfg["decoding"]
        constrained = dec["constraint_mode"] == "constrained"
        schema, removed = self._constraint[role]
        history: list[list[tuple[str, str]]] = []
        for k in range(cfg["max_attempts"]):
            req = GenerationRequest(role, prompt, schema if constrained else None, dec, dec["seed"] + k, k)
            raw = self._backend.generate(req)
            try:
                value: Any = json.loads(raw, parse_constant=_reject_constant)
            except (ValueError, TypeError):
                history.append([("", "json")])
                continue
            bad = self._schemas.output_violations(role, value)
            if not bad:
                self.last_call_info = CallInfo(role, k + 1, removed)
                log.debug("role=%s attempts=%d", role, k + 1)
                return value
            history.append(bad)
        self.last_call_info = CallInfo(role, len(history), removed)
        log.warning("role=%s 출력 검증 실패 attempts=%d", role, len(history))
        raise ModelOutputInvalid(role, len(history), history)

    def parse(self, input: Mapping) -> dict:
        return self._run("LORA_A", input)

    def generate(self, input: Mapping) -> dict:
        return self._run("LORA_B", input)


def _reject_constant(name: str):
    raise ValueError(name)


def _build(serving_config: Mapping, expected: str, backend: ConstraintBackend, schema_dir) -> _Session:
    _check_config(serving_config, backend)
    h = serving_config_hash(serving_config)
    if h != expected:
        raise ServingConfigMismatch("serving_config_hash가 expected_serving_config_hash와 다르다")
    return _Session(serving_config, backend, SchemaSet(schema_dir), h)


def load(adapters: Mapping[str, Path], serving_config: Mapping, *, expected_serving_config_hash: str,
         backend: ConstraintBackend | None = None, schema_dir: Path | None = None) -> ModelSession:
    """I9 load. backend, schema_dir는 I9에 없는 선택 인자이며 시험과 조립용이다."""
    if set(adapters) != set(INPUT_CONTRACT):
        raise ContractViolation("adapters는 LORA_A와 LORA_B를 모두 가져야 한다")
    for role, p in adapters.items():
        if not Path(p).is_dir():
            raise ContractViolation(f"{role} 어댑터 디렉터리가 없다")
    if backend is None:
        name = serving_config.get("constraint", {}).get("backend") if isinstance(serving_config, Mapping) else None
        raise BackendNotAvailable(f"백엔드 {name!r}는 이 환경에 구현·설치되어 있지 않다")
    return _build(serving_config, expected_serving_config_hash, backend, schema_dir)


class FakeModelSession(_Session):
    """모델 없이 호출 경로를 시험한다. 실제 세션과 같은 검증·재시도 코드를 거치고 백엔드만 FakeBackend다."""

    def __init__(self, serving_config: Mapping, *, expected_serving_config_hash: str,
                 backend: FakeBackend | None = None, schema_dir: Path | None = None):
        backend = backend or FakeBackend()
        _check_config(serving_config, backend)
        h = serving_config_hash(serving_config)
        if h != expected_serving_config_hash:
            raise ServingConfigMismatch("serving_config_hash가 expected_serving_config_hash와 다르다")
        super().__init__(serving_config, backend, SchemaSet(schema_dir), h)
        self.backend = backend


def fake_serving_config(backend: FakeBackend | None = None, **overrides) -> dict:
    """FakeBackend에 맞는 유효한 serving_config(시험·예시용)."""
    b = backend or FakeBackend()
    cfg = {
        "prompt_template_version": "prompt-1",
        "decoding": {"constraint_mode": "constrained", "temperature": 0, "seed": 1000, "max_tokens": 512},
        "max_attempts": 3,
        "constraint": {"backend": b.name, "backend_version": b.version,
                       "supported_features": sorted(b.supported_features),
                       "relax_policy_version": RELAX_POLICY_VERSION},
        "output_contract": SUPPORTED_RUNTIME_CONTRACT_VERSION,
    }
    cfg.update(overrides)
    return cfg
