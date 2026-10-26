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
    input: Mapping[str, Any] | None = None   # 모델 입력 객체(프롬프트의 user 내용). 가짜 백엔드가 문항을 읽는 데만 쓰고, 실제 백엔드는 쓰지 않는다


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


# 가짜 백엔드의 출력. LoRA-A: 문항 순서(flow)를 받았으면 입력 question_id의 다음 문항으로 가는 NEXT(values 비어 있음),
# 못 받았으면 고정 출력(FAKE_EXTRACTION, 호환). LoRA-B는 문장 그대로의 텍스트다.
FAKE_EXTRACTION = {"action": "NEXT", "next_item": "END", "values": []}
FAKE_RESPONSE_TEXT = "(가짜 응답)"


def load_fake_flow(path: str) -> list[str]:
    """문항 ID 목록 JSON(`["Q1.D01", ...]` 또는 `{"order": [...]}`)을 읽는다. 형식이 틀리면 ValueError."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    order = data.get("order") if isinstance(data, dict) else data
    if not isinstance(order, list) or not order or not all(isinstance(x, str) and x for x in order) or len(set(order)) != len(order):
        raise ValueError("문항 순서 파일은 중복 없는 문항 ID 문자열의 비어 있지 않은 목록이어야 한다")
    return list(order)


def fake_extraction_for(question_id: str, order: Sequence[str]) -> dict:
    """question_id 다음 문항으로 가는 최소 유효 ActionExtraction. values가 비어 있으면 저장 값이 없어 건너뛰기 규칙이 적용되지 않으므로
    기대 next_item은 순서상 바로 다음 문항(마지막이면 END)이다(turn-engine v0.3.0 validate_extraction과 일치)."""
    order = list(order)
    nxt = order[order.index(question_id) + 1] if question_id in order and order.index(question_id) + 1 < len(order) else "END"
    return {"action": "NEXT", "next_item": nxt, "values": []}


def make_default_responder(order: Sequence[str] | None = None, extraction: Mapping | None = None) -> Callable[[GenerationRequest], str]:
    """extraction(고정 출력)이 있으면 그것, 아니면 order가 있으면 입력 기준 다음 문항, 둘 다 없으면 FAKE_EXTRACTION."""
    fixed = json.dumps(extraction, ensure_ascii=False) if extraction is not None else None

    def respond(req: GenerationRequest) -> str:
        if req.role != "LORA_A":
            return FAKE_RESPONSE_TEXT
        if fixed is not None:
            return fixed
        qid = (req.input or {}).get("question_id") if order else None
        if order and isinstance(qid, str):
            return json.dumps(fake_extraction_for(qid, order), ensure_ascii=False)
        return json.dumps(FAKE_EXTRACTION, ensure_ascii=False)
    return respond


_default_responder = make_default_responder()
