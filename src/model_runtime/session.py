"""ModelSession: 렌더 → 생성 → 사후 검증 → 재시도 → (respond만) 대체 문장. FakeModelSession도 같은 코드를 쓴다."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from boundary_contracts.canonical import file_hash

from .backends import ConstraintBackend, FakeBackend, GenerationRequest
from .errors import (BackendNotAvailable, ContractViolation, ModelOutputInvalid, ServingConfigMismatch,
                     SystemPromptMismatch, UnsupportedServingConfig)
from .fallback import fallback_text
from .prompts import DEFAULT_TEMPLATE_VERSION, SUPPORTED_TEMPLATE_VERSIONS, render_prompt
from .protection import PROTECTION_POLICIES, PROTECTION_POLICY_VERSION
from .schema_guard import (INPUT_CONTRACT, JSON_OUTPUT_ROLES, OUTPUT_CONTRACT, RELAX_POLICY_VERSION,
                           SUPPORTED_RUNTIME_CONTRACT_VERSION, SchemaSet, relax)
from .serving_config import serving_config_hash, validate_serving_config

RUNTIME_VERSION = "0.7.6"
log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.

# FakeModelSession이 시스템 프롬프트를 따로 받지 않을 때 쓰는 시험용 문장. 학습과 짝인 프롬프트가 아니다.
FAKE_SYSTEM_PROMPTS = {"LORA_A": "(가짜 모델용 시스템 프롬프트: LoRA-A)", "LORA_B": "(가짜 모델용 시스템 프롬프트: LoRA-B)"}


@dataclass(frozen=True)
class RawResult:
    """재시도를 거친 모델 출력(평가용). output은 LORA_A면 JSON으로 읽힌 값(읽지 못하면 None), LORA_B면 문장(str).
    errors는 원문 없는 오류 이름. 마지막 시도의 값이며 valid가 아니면 서비스가 쓰지 못하는 출력이다."""
    output: Any
    valid: bool
    errors: tuple = ()


@dataclass(frozen=True)
class ResponseResult:
    """respond의 결과. is_fallback은 모델이 아니라 코드가 채운다(D36)."""
    text: str
    is_fallback: bool


@dataclass(frozen=True)
class CallInfo:
    """마지막 호출의 메타데이터. 원문을 담지 않는다.

    outcome: ok | fallback(respond의 대체 문장) | invalid(extract가 끝내 실패해 ModelOutputInvalid) | raw(*_raw 호출).
    protections: (이름, 건수) 목록. 이름은 docs/rules.md의 G0(respond 형식 위반), G5(대체 문장 사용)."""
    role: str
    attempts: int
    relaxed: tuple
    outcome: str = "ok"
    protections: tuple = ()


class ModelSession(Protocol):
    serving_config_hashes: Mapping[str, str]   # 역할별(D45)
    system_prompt_hashes: Mapping[str, str]    # 역할별, 패키지 프롬프트 파일 바이트의 sha256(D75)
    last_call_info: CallInfo | None

    def extract(self, input: Mapping) -> dict: ...
    def respond(self, input: Mapping) -> ResponseResult: ...
    def extract_raw(self, input: Mapping) -> RawResult: ...
    def respond_raw(self, input: Mapping) -> RawResult: ...


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


class _Session:
    def __init__(self, cfgs: Mapping[str, Mapping], backend: ConstraintBackend, schemas: SchemaSet,
                 hashes: Mapping[str, str], system_prompts: Mapping[str, str]):
        # 두 역할이 한 백엔드(기반 모델 가중치)를 공유하고, 설정(디코딩·보호 정책 등)은 역할마다 따로 쓴다.
        self._cfgs = {r: json.loads(json.dumps(c)) for r, c in cfgs.items()}
        self._backend = backend
        self._schemas = schemas
        self._system_prompts = dict(system_prompts)
        self.serving_config_hashes = dict(hashes)
        self.system_prompt_hashes = {r: "sha256:" + hashlib.sha256(t.encode("utf-8")).hexdigest()
                                     for r, t in self._system_prompts.items()}
        self.last_call_info: CallInfo | None = None
        self._constraint: dict[str, tuple[dict | None, tuple]] = {}
        for role in OUTPUT_CONTRACT:
            raw = schemas.model_output_schema(role) if role in JSON_OUTPUT_ROLES else None
            if raw is None:
                self._constraint[role] = (None, ())
            else:
                relaxed, removed = relax(raw, backend.supported_features)
                self._constraint[role] = (relaxed, tuple(removed))

    # --- 한 번의 시도가 유효한지 ---
    def _errors(self, role: str, value: Any) -> list[str]:
        return [f"schema:{p or '/'}:{k}" for p, k in self._schemas.output_violations(role, value)]

    def _attempts(self, role: str, input: Mapping) -> _Attempts:
        cfg = self._cfgs[role]
        prompt = render_prompt(role, input, serving_config=cfg, system_prompt=self._system_prompts[role],
                               schema_dir=Path("."), schemas=self._schemas)
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
                GenerationRequest(role, prompt, schema if constrained else None, d, dec["seed"] + k, k, input))
            if role in JSON_OUTPUT_ROLES:
                try:
                    value = json.loads(raw, parse_constant=_reject_constant)
                except (ValueError, TypeError):
                    value, errs = None, ["json"]
                    continue
            else:
                value = raw.strip() if isinstance(raw, str) else raw   # 문장 그대로. 앞뒤 공백은 형식에 포함하지 않는다(J1)
            errs = self._errors(role, value)
            if not errs:
                break
        return _Attempts(value, errs, n)

    def _raw(self, role: str, input: Mapping) -> RawResult:
        a = self._attempts(role, input)
        self.last_call_info = CallInfo(role, a.attempts, self._constraint[role][1], "raw")
        return RawResult(copy.deepcopy(a.output), not a.errors, tuple(a.errors))

    def extract_raw(self, input: Mapping) -> RawResult:
        return self._raw("LORA_A", input)

    def respond_raw(self, input: Mapping) -> RawResult:
        return self._raw("LORA_B", input)

    def extract(self, input: Mapping) -> dict:
        """항상 ActionExtraction을 지키는 값을 낸다. 재시도 후에도 못 지키면 값을 지어내지 않고 ModelOutputInvalid(D75)."""
        a = self._attempts("LORA_A", input)
        removed = self._constraint["LORA_A"][1]
        if a.errors:
            self.last_call_info = CallInfo("LORA_A", a.attempts, removed, "invalid")
            log.warning("role=LORA_A 출력이 형식을 지키지 못함 attempts=%s", a.attempts)
            raise ModelOutputInvalid(a.attempts, tuple(a.errors))
        self.last_call_info = CallInfo("LORA_A", a.attempts, removed)
        return a.output

    def respond(self, input: Mapping) -> ResponseResult:
        a = self._attempts("LORA_B", input)
        removed = self._constraint["LORA_B"][1]
        if not a.errors:
            result = ResponseResult(a.output, False)
            self.last_call_info = CallInfo("LORA_B", a.attempts, removed)
        else:
            result = ResponseResult(fallback_text(input, policy_version=self._cfgs["LORA_B"]["protection_policy_version"]), True)
            self.last_call_info = CallInfo("LORA_B", a.attempts, removed, "fallback", (("G0", 1), ("G5", 1)))
            log.warning("role=LORA_B 대체 문장 사용 attempts=%s", a.attempts)
        if self._schemas.output_violations("LORA_B", result.text):
            raise RuntimeError("대체 문장이 ResponseText를 통과하지 못한다(구현 오류)")
        return result


def _check_roles(adapters, serving_configs, expected_hashes, system_prompts=None) -> None:
    roles = set(INPUT_CONTRACT)
    pairs = [("adapters", adapters), ("serving_configs", serving_configs), ("expected_serving_config_hashes", expected_hashes)]
    if system_prompts is not None:
        pairs.append(("system_prompts", system_prompts))
    for name, m in pairs:
        if set(m) != roles:
            raise ContractViolation(f"{name}의 역할 집합이 LORA_A, LORA_B와 다르다")


def read_system_prompt(role: str, adapter_dir: Path, ref: Mapping) -> str:
    """어댑터 디렉터리 안의 시스템 프롬프트 파일을 읽는다. ref는 SystemPromptRef({path, sha256}).
    파일이 없거나 디렉터리 밖을 가리키거나 해시(canonical.file_hash)가 다르거나 비어 있으면 SystemPromptMismatch."""
    try:
        base = Path(adapter_dir).resolve()
        f = base.joinpath(*str(ref["path"]).split("/")).resolve()
        if not f.is_relative_to(base) or not f.is_file() or file_hash(f) != ref["sha256"]:
            raise SystemPromptMismatch(role)
        text = f.read_bytes().decode("utf-8")
    except (KeyError, TypeError, OSError, UnicodeDecodeError):
        raise SystemPromptMismatch(role) from None
    if not text.strip():
        raise SystemPromptMismatch(role, f"{role}의 시스템 프롬프트 파일이 비어 있다")
    return text


def _build(serving_configs: Mapping[str, Mapping], expected: Mapping[str, str], backend: ConstraintBackend,
           schema_dir, system_prompts: Mapping[str, str]) -> _Session:
    hashes = {}
    for role in INPUT_CONTRACT:                       # 역할마다 형식 → 지원 여부 → 해시 순서
        _check_config(role, serving_configs[role], backend)
        hashes[role] = serving_config_hash(serving_configs[role])
        if hashes[role] != expected[role]:
            raise ServingConfigMismatch(role)
    return _Session(serving_configs, backend, SchemaSet(schema_dir), hashes, system_prompts)


def load(adapters: Mapping[str, Path], serving_configs: Mapping[str, Mapping], *, schema_dir: Path,
         expected_serving_config_hashes: Mapping[str, str], system_prompts: Mapping[str, Mapping],
         backend: ConstraintBackend | None = None) -> ModelSession:
    """I9 load(D45: 설정과 기대 해시는 역할마다). backend는 시험과 조립용 선택 인자다.

    system_prompts: 역할 → SystemPromptRef({path, sha256}) = ReleaseManifest.adapters[role].system_prompt(D75).
    파일은 adapters[role] 디렉터리 안에서 읽는다. INTERFACES I9의 load 시그니처에는 없는 인자이며 추가를 요청했다(change-requests/004)."""
    _check_roles(adapters, serving_configs, expected_serving_config_hashes, system_prompts)
    for role, p in adapters.items():
        if not Path(p).is_dir():
            raise ContractViolation(f"{role} 어댑터 디렉터리가 없다")
    prompts = {role: read_system_prompt(role, Path(adapters[role]), system_prompts[role]) for role in INPUT_CONTRACT}
    if backend is None:
        names = {str(c.get("constraint", {}).get("backend")) for c in serving_configs.values() if isinstance(c, Mapping)}
        if names == {"vllm"}:
            from .vllm_backend import VLLMBackend   # vLLM이 없으면 BackendNotAvailable
            backend = VLLMBackend(adapters)
        else:
            raise BackendNotAvailable(f"백엔드 {', '.join(sorted(names))}는 이 환경에 구현·설치되어 있지 않다")
    return _build(serving_configs, expected_serving_config_hashes, backend, schema_dir, prompts)


class FakeModelSession(_Session):
    """모델 없이 호출 경로를 시험한다. 실제 세션과 같은 검증·재시도·대체 문장 코드를 거치고 백엔드만 FakeBackend다.

    system_prompts: 역할 → 시스템 프롬프트 문장. 없으면 FAKE_SYSTEM_PROMPTS(시험용)."""

    def __init__(self, serving_configs: Mapping[str, Mapping], *, schema_dir: Path,
                 expected_serving_config_hashes: Mapping[str, str], backend: FakeBackend | None = None,
                 system_prompts: Mapping[str, str] | None = None):
        backend = backend or FakeBackend()
        prompts = dict(system_prompts or FAKE_SYSTEM_PROMPTS)
        _check_roles(serving_configs, serving_configs, expected_serving_config_hashes, prompts)
        built = _build(serving_configs, expected_serving_config_hashes, backend, schema_dir, prompts)
        super().__init__(serving_configs, backend, built._schemas, built.serving_config_hashes, prompts)
        self.backend = backend


def fake_serving_config(backend: FakeBackend | None = None, **overrides) -> dict:
    """FakeBackend에 맞는 유효한 serving_config(시험·예시용)."""
    b = backend or FakeBackend()
    cfg = {
        "prompt_template_version": DEFAULT_TEMPLATE_VERSION,
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
