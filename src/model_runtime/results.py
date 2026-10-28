"""역할 호출의 결과 타입. 세션과 두 역할이 함께 쓴다(session.py가 다시 내보낸다)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
