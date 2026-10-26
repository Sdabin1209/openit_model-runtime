"""교체 가능한 제약 디코딩 백엔드."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class GenerationRequest:
    role: str
    prompt: str
    constraint_schema: dict | None   # constraint_mode가 "none"이거나 텍스트를 내는 역할(LORA_B)이면 None
    decoding: Mapping[str, Any]      # serving_config.decoding
    seed: int                        # 기본 시드 + 시도 번호
    attempt: int                     # 0부터


class ConstraintBackend(Protocol):
    name: str
    version: str
    supported_features: frozenset

    def generate(self, request: GenerationRequest) -> str: ...


class FakeBackend:
    """GPU 없이 호출 경로를 시험하는 백엔드. 가짜인 것은 이 백엔드 하나다.

    responder(request) -> str. scripted()는 역할별로 시도 순서대로 문자열(또는 예외)을 돌려준다."""

    name = "fake"
    version = "1"

    def __init__(self, responder: Callable[[GenerationRequest], str] | None = None,
                 supported_features: Sequence[str] = ("type", "properties", "required", "enum", "items",
                                                      "anyOf", "$defs", "$ref", "additionalProperties")):
        self.supported_features = frozenset(supported_features)
        self._responder = responder or _default_responder
        self.requests: list[GenerationRequest] = []

    @classmethod
    def scripted(cls, script: Mapping[str, Sequence[str]], **kw) -> "FakeBackend":
        pos = {r: 0 for r in script}

        def respond(req: GenerationRequest) -> str:
            seq = script[req.role]
            i = min(pos[req.role], len(seq) - 1)
            pos[req.role] += 1
            return seq[i]

        return cls(respond, **kw)

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        return self._responder(request)


# 가짜 백엔드의 고정 출력. LoRA-A는 ActionExtraction을 지키는 최소 출력(다음 문항으로 간다), LoRA-B는 문장 그대로의 텍스트다.
FAKE_EXTRACTION = {"action": "NEXT", "next_item": "END", "values": []}
FAKE_RESPONSE_TEXT = "(가짜 응답)"


def _default_responder(req: GenerationRequest) -> str:
    if req.role == "LORA_A":
        return json.dumps(FAKE_EXTRACTION, ensure_ascii=False)
    return FAKE_RESPONSE_TEXT
