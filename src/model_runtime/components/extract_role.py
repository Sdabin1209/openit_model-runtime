"""extract_role 컴포넌트: LoRA-A(추출). ExtractCall -> ExtractOutcome. 자기 입출력 스키마와 업무만 안다(R3·R4, 감사 MR-01).

출력은 JSON(제약 디코딩)이다. 항상 ActionExtraction을 지키고 흐름 규칙·값 규칙도 지키는 값을 낸다. 끝내 못 지키면 값을 지어내지 않고 result=null과
invalid(시도 횟수, 오류 이름)를 낸다(D75). 예외 ModelOutputInvalid로 바꾸는 것은 wiring이다.
재시도 반복(retry_loop)과 출력 계약 검증(io_validate)은 다른 컴포넌트이므로 wiring이 `attempts`, `output_violations`로 주입한다."""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Callable, Mapping

from boundary_contracts.action_rules import apply_value_rules, check_action_extraction


log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.
INPUT, OUTPUT = "ExtractCall", "ExtractOutcome"
ROLE = "LORA_A"


def _reject_constant(name: str):
    raise ValueError(name)


def _parse(raw: Any):
    try:
        return json.loads(raw, parse_constant=_reject_constant), []
    except (ValueError, TypeError):
        return None, ["json"]


def _schema_violations(output_violations: Callable[[Any], list], value: Any) -> list[str]:
    return [f"schema:{p or '/'}:{k}" for p, k in output_violations(value)]


def _rule_violations(value: Any, call: Mapping, output_violations, box: dict) -> list[str]:
    """스키마 위반이 없을 때만 의미 규칙(계약의 기준 검사기)을 본다. 위반은 "rule:<코드>:<경로>"이며 값은 싣지 않는다.
    왜 여기서: 통과한 값은 호출자가 다시 검사하지 않는다(R7, 계약 ADR 0010). 규칙 본문은 계약의 데이터라 이 영역에 복사하지 않고 기준 검사기를 호출한다.
    3.0.0부터 flow(QuestionFlowTable)와 fields(QuestionFieldSpec)는 항상 있다: 흐름 규칙 뒤에 값 규칙(apply_value_rules)을 적용한다.
    바꿀 수 없는 위반은 위반으로 돌려주어 다시 생성하게 하고, 규칙에 걸려 바뀐 출력은 box["output"]에, 적용 사유는 box["applied"]에 둔다
    (바뀐 출력도 스키마와 의미 규칙을 다시 통과해야 한다)."""
    input, flow, fields = call["input"], call["flow"], call["fields"]
    errs = _schema_violations(output_violations, value)
    if errs:
        return errs
    errs = [f"rule:{v.code}:{v.path}" for v in check_action_extraction(dict(input), value, dict(flow))]
    if errs:
        return errs
    changed, viol, applied = apply_value_rules(dict(input), value, dict(fields), dict(flow))
    if viol:
        return [f"rule:{v.code}:{v.path}" for v in viol]
    if applied:
        again = _schema_violations(output_violations, changed) or [f"rule:AFTER_VALUE_RULES_{v.code}:{v.path}" for v in check_action_extraction(dict(input), changed, dict(flow))]
        if again:
            return again   # 계약 기준 구현끼리 어긋난 것이다. 값을 내보내지 않고 다시 생성한다
        box["output"], box["applied"] = changed, list(applied)
    return []


def _info(attempts: int, relaxed: list, outcome: str, protections: list | None = None) -> dict:
    return {"role": ROLE, "attempts": attempts, "relaxed": relaxed, "outcome": outcome, "protections": protections or []}


def _spec(call: Mapping) -> dict:
    return {"role": ROLE, "prompt": call["prompt"], "constraint_schema": call["constraint_schema"], "decoding": call["decoding"],
            "max_attempts": call["max_attempts"], "input": call["input"], "deadline": call["deadline"]}


def run(call: Mapping, *, attempts: Callable[..., dict], output_violations: Callable[[Any], list]) -> dict:
    """값 규칙에 걸려 출력이 바뀌면 다시 생성하지 않고 바뀐 출력을 낸다(데이터셋 정답 모양). 적용 사유는 로그와 보호 건수에 사유 이름만 남긴다."""
    box: dict = {}

    def check(v):
        box.clear()   # 마지막 시도의 결과만 남긴다
        return _rule_violations(v, call, output_violations, box)
    a = attempts(_spec(call), parse=_parse, check=check)
    output = box["output"] if (not a["errors"] and "output" in box) else a["output"]
    applied = box.get("applied", [])
    relaxed = list(call["relaxed"])
    if a["errors"]:
        log.warning("role=%s 출력이 형식을 지키지 못함 attempts=%s", ROLE, a["attempts"])
        return {"result": None, "info": _info(a["attempts"], relaxed, "invalid"), "invalid": {"attempts": a["attempts"], "errors": list(a["errors"])}}
    if applied:
        log.info("role=%s 값 규칙 적용 reasons=%s attempts=%s", ROLE, ",".join(applied), a["attempts"])   # 값은 남기지 않는다
    return {"result": output, "info": _info(a["attempts"], relaxed, "ok", [list(kv) for kv in sorted(Counter(applied).items())]), "invalid": None}


def run_raw(call: Mapping, *, attempts: Callable[..., dict], output_violations: Callable[[Any], list]) -> dict:
    """원시 결과(평가용)는 스키마 통과 여부만 본다. 흐름·값 규칙은 서버 호출 경로의 일이다. 출력: {"raw": RawResult, "info": CallInfo}."""
    a = attempts(_spec(call), parse=_parse, check=lambda v: _schema_violations(output_violations, v))
    return {"raw": {"output": a["output"], "valid": not a["errors"], "errors": list(a["errors"])}, "info": _info(a["attempts"], list(call["relaxed"]), "raw")}
