"""출력 보호 정책 (D36, D75, docs/rules.md). 순수 함수 없음: 규칙은 정책 한 버전에 묶는다.

2.0.0(D74·D75)에서 v1의 파서 보호(P1~P6)와 생성기 규칙 G1·G4는 없어졌다.
- LoRA-A(extract): 보호하지 않는다. 형식을 못 지키면 값을 지어내지 않고 ModelOutputInvalid(HTTP 503 OUTPUT_INVALID)다.
- LoRA-B(respond): 형식(비어 있지 않은 문장)을 못 지키면 입력 재료를 이어 붙인 대체 문장을 낸다(fallback.py)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectionPolicy:
    """출력 보호 정책 한 버전. 출력을 바꾸는 값과 해석은 모두 여기에 묶고, 바꾸려면 새 버전을 만든다.
    serving_config.protection_policy_version으로 기록되어 serving_config_hash에 반영된다."""
    version: str
    # 대체 문장을 만들 재료(known, ask, hold, next.text)가 하나도 없을 때의 문장. 계약에 없는 문구이며 문구 확정은 도메인 소유자 몫이다(잠정).
    empty_fallback_text: str
    # 계약이 정하지 않아 이 리포지토리가 해석한 곳. 다음 정책 버전에서 바꿀 때 근거가 된다(헤드 확인 대기: change-requests/004).
    judgments: tuple


PROTECTION_POLICIES = {
    "protect-2": ProtectionPolicy(
        version="protect-2",
        empty_fallback_text="죄송합니다. 다시 한 번 말씀해 주세요.",   # 잠정. END처럼 재료가 없는 경우에도 같은 문장이라 어색할 수 있다(미정)
        judgments=(
            "J1 respond 형식: 앞뒤 공백을 뗀 문자열이 비어 있지 않으면 통과한다. 길이 상한은 두지 않는다(계약: 평가 지표, 미정).",
            "J2 대체 문장 조립: known, ask, hold, next.text 순으로 비어 있지 않은 것을 공백 하나로 잇고, 바로 앞과 같은 조각은 한 번만 쓴다. 재료가 없으면 empty_fallback_text.",
            "J3 extract: 보호하지 않는다. 재시도 후에도 스키마를 어기면 ModelOutputInvalid(D75).",
        ),
    ),
}
PROTECTION_POLICY_VERSION = "protect-2"   # 현재 정책. 지원하는 정책은 PROTECTION_POLICIES의 키다.
