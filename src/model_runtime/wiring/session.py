"""ModelSession: 렌더 → 생성 → 사후 검증 → 재시도 → (respond만) 대체 문장. FakeModelSession도 같은 코드를 쓴다."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Protocol

from boundary_contracts.action_rules import load_rules

from ..components import (constraint_relax, extract_role, fallback_text, io_validate, prompt_render, respond_role, retry_loop,
                          serving_config_check, system_prompt_read)
from ..components.fake_backend import FakeBackend
from ..components.fallback_text import PROTECTION_POLICY_VERSION
from ..components.constraint_relax import RELAX_POLICY_VERSION
from ..types.CallInfo import CallInfo
from ..types.RawResult import RawResult
from ..types.ResponseResult import ResponseResult
from .errors import (BackendNotAvailable, ContractViolation, ModelOutputInvalid, ServingConfigMismatch,
                     SystemPromptMismatch, UnsupportedServingConfig)
from .clock import RealClock
from .ports import ConstraintBackend
from .prompt_api import render_prompt
from .schema_gate import gate, violations
from .schema_set import SUPPORTED_RUNTIME_CONTRACT_VERSION, SchemaSet
from ..components.prompt_render import DEFAULT_TEMPLATE_VERSION
from ..components.serving_config_check import serving_config_hash, validate_serving_config

RUNTIME_VERSION = "0.11.4"
# 매니페스트 계보 표지 action_rules_version의 값. 왜 계약 규칙 데이터에서 읽나: 이 서버가 보증하는 LoRA-A 출력 규칙의 정본이 그 데이터라, 리터럴로 적으면 규칙이 바뀔 때 어긋난다(계약 3.0.0, D109).
ACTION_RULES_VERSION = load_rules()["rules_version"]
log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.

# FakeModelSession이 시스템 프롬프트를 따로 받지 않을 때 쓰는 시험용 문장. 학습과 짝인 프롬프트가 아니다.
FAKE_SYSTEM_PROMPTS = {"LORA_A": "(가짜 모델용 시스템 프롬프트: LoRA-A)", "LORA_B": "(가짜 모델용 시스템 프롬프트: LoRA-B)"}


class ModelSession(Protocol):
    serving_config_hashes: Mapping[str, str]   # 역할별(D45)
    system_prompt_hashes: Mapping[str, str]    # 역할별, 패키지 프롬프트 파일 바이트의 sha256(D75)
    last_call_info: CallInfo | None

    def extract(self, input: Mapping, *, deadline: float | None = None) -> dict: ...   # deadline: time.monotonic 기준 호출 마감(선택). 재시도 예산에만 쓴다
    def respond(self, input: Mapping, *, deadline: float | None = None) -> ResponseResult: ...
    def extract_raw(self, input: Mapping) -> RawResult: ...
    def respond_raw(self, input: Mapping) -> RawResult: ...


_UNSUPPORTED = {"/serving_config/output_contract": "지원하지 않는 output_contract",
                "/serving_config/prompt_template_version": "지원하지 않는 프롬프트 템플릿 버전",
                "/serving_config/protection_policy_version": "지원하지 않는 출력 보호 정책 버전",
                "/serving_config/constraint/relax_policy_version": "지원하지 않는 완화 정책 버전"}
_check_serving_config = gate(serving_config_check, UnsupportedServingConfig)
_relax = gate(constraint_relax)
_attempts = gate(retry_loop)
_extract = gate(extract_role)
_extract_raw = gate(extract_role, output="RawOutcome", run="run_raw")
_respond = gate(respond_role)
_respond_raw = gate(respond_role, output="RawOutcome", run="run_raw")
_fallback = gate(fallback_text)
_read_prompt = gate(system_prompt_read, ContractViolation)


def _check_and_hash(role: str, cfg: Mapping, expected: str, backend: ConstraintBackend) -> str:
    """역할마다 형식(계약 ServingConfig) -> 지원 여부(스키마) -> 백엔드 일치·해시(스키마 밖, 컴포넌트) 순서."""
    validate_serving_config(cfg)
    req = {"role": role, "serving_config": dict(cfg), "backend": {"name": backend.name, "version": backend.version, "supported_features": sorted(backend.supported_features)},
           "expected_hash": expected}
    for path, _ in violations("ServingConfigCheckInput", req):
        if path in _UNSUPPORTED:
            raise UnsupportedServingConfig(f"{role}: {_UNSUPPORTED[path]}")
    return _check_serving_config(req)["serving_config_hash"]


def _info(d: Mapping) -> CallInfo:
    return CallInfo(d["role"], d["attempts"], tuple(d["relaxed"]), d["outcome"], tuple((k, n) for k, n in d["protections"]))


class _Session:
    def __init__(self, cfgs: Mapping[str, Mapping], backend: ConstraintBackend, schemas: SchemaSet,
                 hashes: Mapping[str, str], system_prompts: Mapping[str, str]):
        # 두 역할이 한 백엔드(기반 모델 가중치)를 공유하고, 설정(디코딩·보호 정책 등)은 역할마다 따로 쓴다.
        # 역할 안의 일(파싱, 검증, 실패 처리)은 extract_role·respond_role 컴포넌트가 각자 하고, 세션(wiring)은 연결만 한다(MR-01).
        self._cfgs = {r: json.loads(json.dumps(c)) for r, c in cfgs.items()}
        self._backend = backend
        self._schemas = schemas
        self._system_prompts = dict(system_prompts)
        self.serving_config_hashes = dict(hashes)
        self.system_prompt_hashes = {r: "sha256:" + hashlib.sha256(t.encode("utf-8")).hexdigest()
                                     for r, t in self._system_prompts.items()}
        self.last_call_info: CallInfo | None = None
        self.clock = RealClock().monotonic   # 재시도 예산의 시계. 서비스가 레지스트리와 같은 시계로 바꾼다(wiring/service.py, ADR 0029)
        relaxed = _relax({"schema": schemas.model_output_schema("LORA_A"), "supported_features": sorted(backend.supported_features)})
        self._constraint = (relaxed["schema"], list(relaxed["removed"]))

    def _prompt(self, role: str, input: Mapping) -> str:
        return render_prompt(role, input, serving_config=self._cfgs[role], system_prompt=self._system_prompts[role], schema_dir=Path("."), schemas=self._schemas)

    def _ports(self, role: str) -> dict:
        generate = self._backend.generate
        return {"attempts": lambda spec, parse, check: _attempts(spec, generate=generate, parse=parse, check=check, clock=self.clock),
                "output_violations": lambda v: self._schemas.output_violations(role, v)}

    def _extract_call(self, input: Mapping, flow: Mapping, fields: Mapping, deadline, constrained: bool | None = None) -> dict:
        cfg = self._cfgs["LORA_A"]
        dec = cfg["decoding"]
        return {"prompt": self._prompt("LORA_A", input), "input": dict(input), "flow": dict(flow), "fields": dict(fields), "decoding": dec,
                "max_attempts": cfg["max_attempts"], "constraint_schema": self._constraint[0] if dec["constraint_mode"] == "constrained" else None,
                "relaxed": self._constraint[1], "deadline": deadline}

    def _respond_call(self, input: Mapping, deadline) -> dict:
        cfg = self._cfgs["LORA_B"]
        return {"prompt": self._prompt("LORA_B", input), "input": dict(input), "decoding": cfg["decoding"], "max_attempts": cfg["max_attempts"],
                "protection_policy_version": cfg["protection_policy_version"], "deadline": deadline}

    def extract_raw(self, input: Mapping) -> RawResult:
        o = _extract_raw(self._extract_call(input, {}, {}, None), **self._ports("LORA_A"))
        self.last_call_info = _info(o["info"])
        return RawResult(copy.deepcopy(o["raw"]["output"]), o["raw"]["valid"], tuple(o["raw"]["errors"]))

    def respond_raw(self, input: Mapping) -> RawResult:
        o = _respond_raw(self._respond_call(input, None), **self._ports("LORA_B"))
        self.last_call_info = _info(o["info"])
        return RawResult(o["raw"]["output"], o["raw"]["valid"], tuple(o["raw"]["errors"]))

    def extract(self, input: Mapping, flow: Mapping, fields: Mapping, *, deadline: float | None = None) -> dict:
        """출력은 스키마와 계약의 LoRA-A 출력 규칙(flow: QuestionFlowTable)과 값 규칙(fields: QuestionFieldSpec)을 지킨다(3.0.0: 둘 다 필수).
        값 규칙에 걸린 출력은 데이터셋 정답 모양으로 바뀌어 나온다."""
        o = _extract(self._extract_call(input, flow, fields, deadline), **self._ports("LORA_A"))
        self.last_call_info = _info(o["info"])
        if o["invalid"] is not None:
            raise ModelOutputInvalid(o["invalid"]["attempts"], tuple(o["invalid"]["errors"]))
        return o["result"]

    def respond(self, input: Mapping, *, deadline: float | None = None) -> ResponseResult:
        o = _respond(self._respond_call(input, deadline), **self._ports("LORA_B"), fallback=lambda inp, ver: _fallback({"request": inp, "policy_version": ver}))
        self.last_call_info = _info(o["info"])
        return ResponseResult(o["result"]["text"], o["result"]["is_fallback"])


ROLES = ("LORA_A", "LORA_B")


def _check_roles(adapters, serving_configs, expected_hashes, system_prompts=None) -> None:
    pairs = [("adapters", adapters), ("serving_configs", serving_configs), ("expected_serving_config_hashes", expected_hashes)]
    if system_prompts is not None:
        pairs.append(("system_prompts", system_prompts))
    for name, m in pairs:
        if violations("RoleMap", {k: True for k in m}):
            raise ContractViolation(f"{name}의 역할 집합이 LORA_A, LORA_B와 다르다")


def read_system_prompt(role: str, adapter_dir: Path, ref: Mapping) -> str:
    """어댑터 디렉터리 안의 시스템 프롬프트 파일을 읽는다. ref는 SystemPromptRef({path, sha256}).
    파일이 없거나 디렉터리 밖을 가리키거나 해시(canonical.file_hash)가 다르거나 비어 있으면 SystemPromptMismatch."""
    try:
        req = {"role": role, "adapter_dir": str(adapter_dir), "ref": {"path": ref["path"], "sha256": ref["sha256"]}}
    except (KeyError, TypeError):
        raise SystemPromptMismatch(role) from None
    if violations("SystemPromptReadInput", req):
        raise SystemPromptMismatch(role)
    text = gate(system_prompt_read, SystemPromptMismatch, lambda m: SystemPromptMismatch(role, f"{role}의 시스템 프롬프트 파일이 비어 있다"))(req)
    return text


def _build(serving_configs: Mapping[str, Mapping], expected: Mapping[str, str], backend: ConstraintBackend,
           schema_dir, system_prompts: Mapping[str, str]) -> _Session:
    hashes = {role: _check_and_hash(role, serving_configs[role], expected[role], backend) for role in ROLES}
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
    prompts = {role: read_system_prompt(role, Path(adapters[role]), system_prompts[role]) for role in ROLES}
    if backend is None:
        names = {str(c.get("constraint", {}).get("backend")) for c in serving_configs.values() if isinstance(c, Mapping)}
        if names == {"vllm"}:
            from ..components.vllm_backend import VLLMBackend   # vLLM이 없으면 BackendNotAvailable
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
