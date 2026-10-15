"""ModelSession: 렌더 → 생성 → 사후 검증 → 재시도 → 출력 보호. FakeModelSession도 같은 코드를 쓴다."""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .backends import ConstraintBackend, FakeBackend, GenerationRequest
from .errors import BackendNotAvailable, ContractViolation, ServingConfigMismatch, UnsupportedServingConfig
from .fallback import fallback_text
from .prompts import SUPPORTED_TEMPLATE_VERSIONS, render_prompt
from .protection import PROTECTION_POLICIES, PROTECTION_POLICY_VERSION, generator_rule_errors, protect_parser
from .schema_guard import (INPUT_CONTRACT, OUTPUT_CONTRACT, RELAX_POLICY_VERSION,
                           SUPPORTED_RUNTIME_CONTRACT_VERSION, SchemaSet, relax)
from .serving_config import serving_config_hash, validate_serving_config

RUNTIME_VERSION = "0.5.0"
log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.


@dataclass(frozen=True)
class RawResult:
    """보호 전 모델 출력(평가용). output은 JSON으로 읽힌 값(읽지 못하면 None), errors는 원문 없는 오류 이름."""
    output: Any
    valid: bool
    errors: tuple = ()


@dataclass(frozen=True)
class CallInfo:
    """마지막 호출의 메타데이터. 원문을 담지 않는다.

    outcome: ok(보호 없음) | repaired(파서 항목을 고침) | fallback(파서 빈 목록 또는 생성기 대체 문장) | raw(*_raw 호출).
    protections: (이름, 건수) 목록. 이름은 docs/rules.md의 P1~P6, G1·G4(원인), G5(대체 문장)."""
    role: str
    attempts: int
    relaxed: tuple
    outcome: str = "ok"
    protections: tuple = ()


class ModelSession(Protocol):
    serving_config_hashes: Mapping[str, str]   # 역할별(D45)
    last_call_info: CallInfo | None

    def parse(self, input: Mapping) -> dict: ...
    def generate(self, input: Mapping) -> dict: ...
    def parse_raw(self, input: Mapping) -> RawResult: ...
    def generate_raw(self, input: Mapping) -> RawResult: ...


def _check_config(role: str, cfg: Mapping, backend: ConstraintBackend) -> None:
    validate_serving_config(cfg)
    if cfg["output_contract"] != SUPPORTED_RUNTIME_CONTRACT_VERSION:
        raise UnsupportedServingConfig(f"{role}: 지원하지 않는 output_contract")
    if cfg["prompt_template_version"] not in SUPPORTED_TEMPLATE_VERSIONS:
        raise UnsupportedServingConfig(f"{role}: 지원하지 않는 프롬프트 템플릿 버전")
    if cfg["protection_policy_version"] not in PROTECTION_POLICIES:
        raise UnsupportedServingConfig(f"{role}: 지원하지 않는 출력 보호 정책 버전")
    c = cfg["constraint"]
    if c["relax_policy_version"] != RELAX_POLICY_VERSION:
        raise UnsupportedServingConfig(f"{role}: 지원하지 않는 완화 정책 버전")
    if (c["backend"], c["backend_version"]) != (backend.name, backend.version) or \
            frozenset(c["supported_features"]) != frozenset(backend.supported_features):
        raise UnsupportedServingConfig(f"{role}: serving_config.constraint가 사용할 백엔드와 다르다")


def _reject_constant(name: str):
    raise ValueError(name)


@dataclass
class _Attempts:
    output: Any
    errors: list
    attempts: int
    prompt_ok: bool = True


class _Session:
    def __init__(self, cfgs: Mapping[str, Mapping], backend: ConstraintBackend, schemas: SchemaSet,
                 hashes: Mapping[str, str]):
        # 두 역할이 한 백엔드(기반 모델 가중치)를 공유하고, 설정(디코딩·보호 정책 등)은 역할마다 따로 쓴다.
        self._cfgs = {r: json.loads(json.dumps(c)) for r, c in cfgs.items()}
        self._backend = backend
        self._schemas = schemas
        self.serving_config_hashes = dict(hashes)
        self.last_call_info: CallInfo | None = None
        self._constraint: dict[str, tuple[dict, tuple]] = {}
        for role in OUTPUT_CONTRACT:
            relaxed, removed = relax(schemas.model_output_schema(role), backend.supported_features)
            self._constraint[role] = (relaxed, tuple(removed))
        self._intents = schemas.vocabulary("ParserOutput", "Intent")
        self._progress_ops = schemas.vocabulary("ParserOutput", "ProgressOp")

    # --- 한 번의 시도가 유효한지 ---
    def _errors(self, role: str, value: Any, input: Mapping) -> list[str]:
        if role == "LORA_A":
            errs = [f"schema:{p or '/'}:{k}" for p, k in self._schemas.output_violations(role, value)]
            _, hits = self._protect_parser(value, input)
            return errs + [f"rule:{name}" for name in sorted(hits)]
        errs = [f"schema:{p or '/'}:{k}" for p, k in self._schemas.model_output_violations(role, value)]
        if not errs:
            errs = generator_rule_errors(value["response_text"], input)
        return errs

    def _protect_parser(self, value: Any, input: Mapping):
        return protect_parser(
            value, intents=self._intents, progress_ops=self._progress_ops,
            guide_ids=[g["guide_id"] for g in input["guide_list"]],
            schema_ok=lambda r: not self._schemas.output_violations("LORA_A", r))

    def _attempts(self, role: str, input: Mapping) -> _Attempts:
        cfg = self._cfgs[role]
        prompt = render_prompt(role, input, serving_config=cfg, schema_dir=Path("."), schemas=self._schemas)
        dec = cfg["decoding"]
        constrained = dec["constraint_mode"] == "constrained"
        schema, _ = self._constraint[role]
        value: Any = None
        errs: list[str] = []
        n = 0
        for k in range(cfg["max_attempts"]):
            n = k + 1
            d = dec
            if k > 0 and "retry_temperature" in dec:   # 재시도 온도(§2.3). 시도 0은 항상 temperature
                d = {**dec, "temperature": dec["retry_temperature"]}
            raw = self._backend.generate(
                GenerationRequest(role, prompt, schema if constrained else None, d, dec["seed"] + k, k))
            try:
                value = json.loads(raw, parse_constant=_reject_constant)
            except (ValueError, TypeError):
                value, errs = None, ["json"]
                continue
            errs = self._errors(role, value, input)
            if not errs:
                break
        return _Attempts(value, errs, n)

    def _raw(self, role: str, input: Mapping) -> RawResult:
        a = self._attempts(role, input)
        self.last_call_info = CallInfo(role, a.attempts, self._constraint[role][1], "raw")
        return RawResult(copy.deepcopy(a.output), not a.errors, tuple(a.errors))

    def parse_raw(self, input: Mapping) -> RawResult:
        return self._raw("LORA_A", input)

    def generate_raw(self, input: Mapping) -> RawResult:
        return self._raw("LORA_B", input)

    def parse(self, input: Mapping) -> dict:
        a = self._attempts("LORA_A", input)
        removed = self._constraint["LORA_A"][1]
        if not a.errors:
            self.last_call_info = CallInfo("LORA_A", a.attempts, removed)
            return a.output
        result, hits = self._protect_parser(a.output, input)
        if not hits:   # 규칙 해당은 없는데 스키마만 통과하지 못한 경우는 protect가 P1로 처리한다. 여기 오면 불변식 위반이다.
            raise RuntimeError("출력 보호가 아무것도 적용하지 않았는데 출력이 유효하지 않다")
        outcome = "fallback" if "P1" in hits else "repaired"
        self.last_call_info = CallInfo("LORA_A", a.attempts, removed, outcome, tuple(sorted(hits.items())))
        log.warning("role=LORA_A 출력 보호 적용 protections=%s", dict(hits))
        self._assert_valid("LORA_A", result)
        return result

    def generate(self, input: Mapping) -> dict:
        a = self._attempts("LORA_B", input)
        removed = self._constraint["LORA_B"][1]
        if not a.errors:
            result = {"response_text": a.output["response_text"], "is_fallback": False}
            self.last_call_info = CallInfo("LORA_B", a.attempts, removed)
        else:
            causes = sorted({e.split(":")[1] for e in a.errors if e.startswith("rule:")}) or ["G0"]
            result = {"response_text": fallback_text(input, policy_version=self._cfgs["LORA_B"]["protection_policy_version"]),
                      "is_fallback": True}
            self.last_call_info = CallInfo("LORA_B", a.attempts, removed, "fallback",
                                           tuple((c, 1) for c in causes) + (("G5", 1),))
            log.warning("role=LORA_B 대체 문장 사용 causes=%s", causes)
        self._assert_valid("LORA_B", result)
        return result

    def _assert_valid(self, role: str, result: Any) -> None:
        if self._schemas.output_violations(role, result):
            raise RuntimeError("보호한 출력이 출력 계약을 통과하지 못한다(구현 오류)")


def _check_roles(adapters, serving_configs, expected_hashes) -> None:
    roles = set(INPUT_CONTRACT)
    for name, m in (("adapters", adapters), ("serving_configs", serving_configs),
                    ("expected_serving_config_hashes", expected_hashes)):
        if set(m) != roles:
            raise ContractViolation(f"{name}의 역할 집합이 LORA_A, LORA_B와 다르다")


def _build(serving_configs: Mapping[str, Mapping], expected: Mapping[str, str], backend: ConstraintBackend,
           schema_dir) -> _Session:
    hashes = {}
    for role in INPUT_CONTRACT:                       # 역할마다 형식 → 지원 여부 → 해시 순서
        _check_config(role, serving_configs[role], backend)
        hashes[role] = serving_config_hash(serving_configs[role])
        if hashes[role] != expected[role]:
            raise ServingConfigMismatch(role)
    return _Session(serving_configs, backend, SchemaSet(schema_dir), hashes)


def load(adapters: Mapping[str, Path], serving_configs: Mapping[str, Mapping], *, schema_dir: Path,
         expected_serving_config_hashes: Mapping[str, str], backend: ConstraintBackend | None = None) -> ModelSession:
    """I9 load(D45: 설정과 기대 해시는 역할마다). backend는 시험과 조립용 선택 인자다."""
    _check_roles(adapters, serving_configs, expected_serving_config_hashes)
    for role, p in adapters.items():
        if not Path(p).is_dir():
            raise ContractViolation(f"{role} 어댑터 디렉터리가 없다")
    if backend is None:
        names = {str(c.get("constraint", {}).get("backend")) for c in serving_configs.values() if isinstance(c, Mapping)}
        if names == {"vllm"}:
            from .vllm_backend import VLLMBackend   # vLLM이 없으면 BackendNotAvailable
            backend = VLLMBackend(adapters)
        else:
            raise BackendNotAvailable(f"백엔드 {', '.join(sorted(names))}는 이 환경에 구현·설치되어 있지 않다")
    return _build(serving_configs, expected_serving_config_hashes, backend, schema_dir)


class FakeModelSession(_Session):
    """모델 없이 호출 경로를 시험한다. 실제 세션과 같은 검증·재시도·보호 코드를 거치고 백엔드만 FakeBackend다."""

    def __init__(self, serving_configs: Mapping[str, Mapping], *, schema_dir: Path,
                 expected_serving_config_hashes: Mapping[str, str], backend: FakeBackend | None = None):
        backend = backend or FakeBackend()
        _check_roles(serving_configs, serving_configs, expected_serving_config_hashes)
        built = _build(serving_configs, expected_serving_config_hashes, backend, schema_dir)
        super().__init__(serving_configs, backend, built._schemas, built.serving_config_hashes)
        self.backend = backend


def fake_serving_config(backend: FakeBackend | None = None, **overrides) -> dict:
    """FakeBackend에 맞는 유효한 serving_config(시험·예시용)."""
    b = backend or FakeBackend()
    cfg = {
        "prompt_template_version": "prompt-2",
        "decoding": {"constraint_mode": "constrained", "temperature": 0, "seed": 1000, "max_tokens": 512},
        "max_attempts": 3,
        "constraint": {"backend": b.name, "backend_version": b.version,
                       "supported_features": sorted(b.supported_features),
                       "relax_policy_version": RELAX_POLICY_VERSION},
        "output_contract": SUPPORTED_RUNTIME_CONTRACT_VERSION,
        "protection_policy_version": PROTECTION_POLICY_VERSION,
    }
    cfg.update(overrides)
    return cfg


def fake_serving_configs(backend: FakeBackend | None = None, *, LORA_A: Mapping | None = None,
                         LORA_B: Mapping | None = None) -> dict:
    """두 역할의 유효한 serving_config(시험·예시용). 역할별로 최상위 키를 덮어쓸 수 있다."""
    b = backend or FakeBackend()
    return {"LORA_A": fake_serving_config(b, **(LORA_A or {})), "LORA_B": fake_serving_config(b, **(LORA_B or {}))}
