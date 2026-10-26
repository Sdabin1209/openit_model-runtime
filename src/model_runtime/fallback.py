"""LoRA-B 대체 문장: 응답 요청의 문장 재료를 그대로 읽는다. 순수 함수이고 발화(user_utterance)를 쓰지 않는다.

계약(INTERFACES I9): 입력의 재료(`known`, `ask`, `hold`, `next.text`)를 이어 붙여 읽는 문장. 순서와 중복 처리는
이 리포지토리의 해석이다(protection.py의 J2, docs/change-requests/004)."""
from __future__ import annotations

from typing import Mapping

from .protection import PROTECTION_POLICIES, PROTECTION_POLICY_VERSION


def fallback_text(request: Mapping, *, policy_version: str = PROTECTION_POLICY_VERSION) -> str:
    """policy_version의 정책으로 대체 문장을 조립한다."""
    policy = PROTECTION_POLICIES[policy_version]
    nxt = request.get("next")
    pieces = [request.get("known"), request.get("ask"), request.get("hold"), nxt.get("text") if isinstance(nxt, Mapping) else None]
    kept: list[str] = []
    for p in pieces:
        if isinstance(p, str) and p.strip() and (not kept or kept[-1] != p.strip()):
            kept.append(p.strip())
    return " ".join(kept) or policy.empty_fallback_text
