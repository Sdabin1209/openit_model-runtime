"""LoRA-A(추출)와 LoRA-B(응답) 두 컴포넌트. 각자 자기 입출력 스키마와 업무만 안다(R3·R4, 감사 MR-01).

- `ExtractRole`: 입력 `ActionExtractionInput` -> 출력 `ActionExtraction`(JSON, 제약 디코딩). 끝내 형식을 못 지키면 `ModelOutputInvalid`(값을 지어내지 않는다, D75).
- `RespondRole`: 입력 `ResponseRequest` -> 출력 `ResponseText`(문장 그대로, 제약 없음). 끝내 못 지키면 대체 문장(D36).
- 둘은 같은 모양(`call`, `raw`)으로 세션이 부르고, 서로를 import하지 않는다. 재시도 반복은 두 역할이 같은 계약(INTERFACES I9)이므로 공용 `run_attempts` 한 곳에 둔다.
- 한 엔진에 두 어댑터를 올리는 구조(ADR 0003)는 그대로다: 역할 이름은 백엔드가 어댑터를 고르는 이름(`GenerationRequest.role`)으로만 쓰인다."""
from __future__ import annotations

import copy
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .backends import ConstraintBackend, GenerationRequest
from .errors import ModelOutputInvalid
from .fallback import fallback_text
from .prompts import render_prompt
from .results import CallInfo, RawResult, ResponseResult
from .schema_guard import SchemaSet, relax
from boundary_contracts.action_rules import apply_value_rules, check_action_extraction

log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.


@dataclass(frozen=True)
class Attempts:
    output: Any
    errors: tuple
    attempts: int


def _reject_constant(name: str):
    raise ValueError(name)


def run_attempts(backend: ConstraintBackend, *, role: str, prompt: str, constraint_schema: dict | None, cfg: Mapping,
                 input: Mapping, parse: Callable[[Any], tuple[Any, list]], check: Callable[[Any], list], deadline: float | None = None) -> Attempts:
    """공용 재시도 반복: `max_attempts`번까지, 시도마다 시드를 1씩 올리고 재시도 온도를 쓴다(§2.3). parse(raw) -> (값, 파싱 오류 목록), check(값) -> 위반 목록.
    왜 한 곳: 시도 횟수·시드·온도 규칙은 두 역할이 같은 계약이라 따로 두면 어긋난다(ADR 0007).
    deadline(time.monotonic 기준)이 있으면 재시도는 남은 예산이 직전 시도에 걸린 시간 이상일 때만 한다(계약 '시간 상한 사슬' 2.2.0, ADR 0020).
    왜 직전 시도 시간인가: 호출 상한 자체와 비교하면 호출 안에서는 재시도가 영영 안 된다. 직전 시도 시간은 다음 시도가 걸릴 시간의 유일한 관측값이다(추정, 측정 안 함)."""
    dec = cfg["decoding"]
    value: Any = None
    errs: list = []
    n = 0
    last_s = 0.0
    for k in range(cfg["max_attempts"]):
        if k > 0 and deadline is not None and deadline - time.monotonic() < last_s:
            break   # 남은 예산이 모자라면 재시도하지 않고 마지막 결과로 실패 처리한다
        n = k + 1
        t0 = time.monotonic()
        d = dec
        if k > 0 and "retry_temperature" in dec:   # 재시도 온도. 시도 0은 항상 temperature
            d = {**dec, "temperature": dec["retry_temperature"]}
        raw = backend.generate(GenerationRequest(role, prompt, constraint_schema, d, dec["seed"] + k, k, input))
        last_s = time.monotonic() - t0
        value, perr = parse(raw)
        if perr:
            errs = perr
            continue
        errs = check(value)
        if not errs:
            break
    return Attempts(value, tuple(errs), n)


@dataclass(frozen=True)
class Outcome:
    """역할 한 번 호출의 결과. result는 서비스가 쓰는 값, error는 호출자에게 던질 예외(없으면 None)."""
    result: Any
    info: CallInfo
    error: Exception | None = None


class _RoleBase:
    name: str

    def __init__(self, cfg: Mapping, system_prompt: str, schemas: SchemaSet, backend_features):
        self._cfg = json.loads(json.dumps(cfg))
        self._system_prompt = system_prompt
        self._schemas = schemas

    def _prompt(self, input: Mapping) -> str:
        return render_prompt(self.name, input, serving_config=self._cfg, system_prompt=self._system_prompt,
                             schema_dir=Path("."), schemas=self._schemas)

    def _violations(self, value: Any) -> list[str]:
        return [f"schema:{p or '/'}:{k}" for p, k in self._schemas.output_violations(self.name, value)]


class ExtractRole(_RoleBase):
    """LoRA-A. 출력은 JSON이다."""
    name = "LORA_A"

    def __init__(self, cfg: Mapping, system_prompt: str, schemas: SchemaSet, backend_features):
        super().__init__(cfg, system_prompt, schemas, backend_features)
        relaxed, removed = relax(schemas.model_output_schema(self.name), backend_features)
        self.constraint = (relaxed, tuple(removed))

    @staticmethod
    def _parse(raw: Any):
        try:
            return json.loads(raw, parse_constant=_reject_constant), []
        except (ValueError, TypeError):
            return None, ["json"]

    def _rule_violations(self, value: Any, input: Mapping, flow: Mapping, fields: Mapping | None = None, box: dict | None = None) -> list[str]:
        """스키마 위반이 없을 때만 의미 규칙(계약의 기준 검사기)을 본다. 위반은 "rule:<코드>:<경로>"이며 값은 싣지 않는다.
        왜 여기서: 통과한 값은 호출자가 다시 검사하지 않는다(R7, 계약 ADR 0010). 규칙 본문은 계약의 데이터라 이 영역에 복사하지 않고 기준 검사기를 호출한다.
        fields(QuestionFieldSpec)가 있으면 그 뒤에 값 규칙(apply_value_rules, 계약 2.4.0)을 적용한다. 바꿀 수 없는 위반은 위반으로 돌려주어 다시 생성하게 하고,
        규칙에 걸려 바뀐 출력은 box["output"]에, 적용 사유는 box["applied"]에 둔다(바뀐 출력도 스키마와 의미 규칙을 다시 통과해야 한다)."""
        errs = self._violations(value)
        if errs or flow is None:
            return errs
        errs = [f"rule:{v.code}:{v.path}" for v in check_action_extraction(dict(input), value, dict(flow))]
        if errs or fields is None or box is None:
            return errs
        changed, viol, applied = apply_value_rules(dict(input), value, dict(fields), dict(flow))
        if viol:
            return [f"rule:{v.code}:{v.path}" for v in viol]
        if applied:
            again = self._violations(changed) or [f"rule:AFTER_VALUE_RULES_{v.code}:{v.path}" for v in check_action_extraction(dict(input), changed, dict(flow))]
            if again:
                return again   # 계약 기준 구현끼리 어긋난 것이다. 값을 내보내지 않고 다시 생성한다
            box["output"], box["applied"] = changed, list(applied)
        return []

    def _attempt(self, backend: ConstraintBackend, input: Mapping, deadline: float | None = None, flow: Mapping | None = None,
                 fields: Mapping | None = None) -> tuple[Attempts, list[str]]:
        constrained = self._cfg["decoding"]["constraint_mode"] == "constrained"
        box: dict = {}

        def check(v):
            box.clear()   # 마지막 시도의 결과만 남긴다
            return self._rule_violations(v, input, flow, fields, box)
        a = run_attempts(backend, role=self.name, prompt=self._prompt(input), constraint_schema=self.constraint[0] if constrained else None,
                         cfg=self._cfg, input=input, parse=self._parse, check=check, deadline=deadline)
        if not a.errors and "output" in box:
            a = replace(a, output=box["output"])
        return a, box.get("applied", [])

    def call(self, backend: ConstraintBackend, input: Mapping, flow: Mapping | None = None, fields: Mapping | None = None,
             deadline: float | None = None) -> Outcome:
        """항상 ActionExtraction을 지키는 값을 낸다. flow(QuestionFlowTable)가 있으면 의미 규칙도, fields(QuestionFieldSpec)가 있으면 값 규칙도 지킨다.
        값 규칙에 걸려 출력이 바뀌면 다시 생성하지 않고 바뀐 출력을 낸다(데이터셋 정답 모양). 적용 사유는 로그와 보호 건수에 사유 이름만 남긴다.
        재시도 후에도 못 지키면 값을 지어내지 않고 ModelOutputInvalid."""
        a, applied = self._attempt(backend, input, deadline, flow, fields)
        removed = self.constraint[1]
        if a.errors:
            log.warning("role=%s 출력이 형식을 지키지 못함 attempts=%s", self.name, a.attempts)
            return Outcome(None, _info(self.name, a.attempts, removed, "invalid"), ModelOutputInvalid(a.attempts, a.errors))
        if applied:
            log.info("role=%s 값 규칙 적용 reasons=%s attempts=%s", self.name, ",".join(applied), a.attempts)   # 값은 남기지 않는다
        return Outcome(a.output, _info(self.name, a.attempts, removed, protections=tuple(sorted(Counter(applied).items()))))

    def raw(self, backend: ConstraintBackend, input: Mapping):
        a, _ = self._attempt(backend, input)
        return _raw(a), _info(self.name, a.attempts, self.constraint[1], "raw")


class RespondRole(_RoleBase):
    """LoRA-B. 출력은 문장 그대로의 텍스트이고 제약 디코딩이 없다."""
    name = "LORA_B"
    constraint = (None, ())

    @staticmethod
    def _parse(raw: Any):
        return (raw.strip() if isinstance(raw, str) else raw), []   # 앞뒤 공백은 형식에 포함하지 않는다(J1)

    def _attempt(self, backend: ConstraintBackend, input: Mapping, deadline: float | None = None) -> Attempts:
        return run_attempts(backend, role=self.name, prompt=self._prompt(input), constraint_schema=None,
                            cfg=self._cfg, input=input, parse=self._parse, check=self._violations, deadline=deadline)

    def call(self, backend: ConstraintBackend, input: Mapping, deadline: float | None = None) -> Outcome:
        """못 지키면 입력 재료로 만든 대체 문장(is_fallback=True, 코드가 채운다, D36)."""
        a = self._attempt(backend, input, deadline)
        if not a.errors:
            result, info = ResponseResult(a.output, False), _info(self.name, a.attempts, ())
        else:
            text = fallback_text(input, policy_version=self._cfg["protection_policy_version"])
            result = ResponseResult(text, True)
            info = _info(self.name, a.attempts, (), "fallback", (("G0", 1), ("G5", 1)))   # docs/rules.md: G0 형식 위반, G5 대체 문장 사용
            log.warning("role=%s 대체 문장 사용 attempts=%s", self.name, a.attempts)
        if self._schemas.output_violations(self.name, result.text):
            raise RuntimeError("대체 문장이 ResponseText를 통과하지 못한다(구현 오류)")
        return Outcome(result, info)

    def raw(self, backend: ConstraintBackend, input: Mapping):
        a = self._attempt(backend, input)
        return _raw(a), _info(self.name, a.attempts, (), "raw")


ROLE_TYPES = {ExtractRole.name: ExtractRole, RespondRole.name: RespondRole}


def build_roles(cfgs: Mapping[str, Mapping], system_prompts: Mapping[str, str], schemas: SchemaSet, backend: ConstraintBackend) -> dict:
    """두 역할을 조립한다. 역할마다 따로 쓰는 설정(디코딩·보호 정책)과 시스템 프롬프트를 각자에게 준다."""
    return {name: cls(cfgs[name], system_prompts[name], schemas, backend.supported_features) for name, cls in ROLE_TYPES.items()}


def _info(role, attempts, relaxed, outcome="ok", protections=()):
    return CallInfo(role, attempts, relaxed, outcome, protections)


def _raw(a: Attempts):
    return RawResult(copy.deepcopy(a.output), not a.errors, a.errors)
