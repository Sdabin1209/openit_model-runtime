"""respond_role 컴포넌트: LoRA-B(응답). RespondCall -> RespondOutcome. 자기 입출력 스키마와 업무만 안다(R3·R4).

출력은 문장 그대로의 텍스트이고 제약 디코딩이 없다. 끝내 형식을 못 지키면 입력 재료로 만든 대체 문장(is_fallback=True, 코드가 채운다, D36).
재시도(retry_loop), 출력 계약 검증(io_validate), 대체 문장(fallback_text)은 다른 컴포넌트이므로 wiring이 주입한다."""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping


log = logging.getLogger("model_runtime")  # 발화·출력 원문은 남기지 않는다.
INPUT, OUTPUT = "RespondCall", "RespondOutcome"
ROLE = "LORA_B"


def _parse(raw: Any):
    return (raw.strip() if isinstance(raw, str) else raw), []   # 앞뒤 공백은 형식에 포함하지 않는다(J1)


def _spec(call: Mapping) -> dict:
    return {"role": ROLE, "prompt": call["prompt"], "constraint_schema": None, "decoding": call["decoding"],
            "max_attempts": call["max_attempts"], "input": call["input"], "deadline": call["deadline"]}


def _violations(output_violations, value) -> list[str]:
    return [f"schema:{p or '/'}:{k}" for p, k in output_violations(value)]


def _info(attempts: int, outcome: str = "ok", protections: list | None = None) -> dict:
    return {"role": ROLE, "attempts": attempts, "relaxed": [], "outcome": outcome, "protections": protections or []}


def run(call: Mapping, *, attempts: Callable[..., dict], output_violations: Callable[[Any], list], fallback: Callable[[Mapping, str], str]) -> dict:
    a = attempts(_spec(call), parse=_parse, check=lambda v: _violations(output_violations, v))
    if not a["errors"]:
        result, info = {"text": a["output"], "is_fallback": False}, _info(a["attempts"])
    else:
        result = {"text": fallback(call["input"], call["protection_policy_version"]), "is_fallback": True}
        info = _info(a["attempts"], "fallback", [["G0", 1], ["G5", 1]])   # docs/rules.md: G0 형식 위반, G5 대체 문장 사용
        log.warning("role=%s 대체 문장 사용 attempts=%s", ROLE, a["attempts"])
    if output_violations(result["text"]):
        raise RuntimeError("대체 문장이 ResponseText를 통과하지 못한다(구현 오류)")
    return {"result": result, "info": info}


def run_raw(call: Mapping, *, attempts: Callable[..., dict], output_violations: Callable[[Any], list]) -> dict:
    a = attempts(_spec(call), parse=_parse, check=lambda v: _violations(output_violations, v))
    return {"raw": {"output": a["output"], "valid": not a["errors"], "errors": list(a["errors"])}, "info": _info(a["attempts"], "raw")}
