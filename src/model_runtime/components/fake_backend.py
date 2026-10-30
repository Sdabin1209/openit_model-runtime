"""fake_backend 컴포넌트: GPU 없이 호출 경로를 시험하는 가짜 백엔드. GenerationRequest -> GeneratedText.

가짜인 것은 이 백엔드 하나다. 응답기(responder)와 문항 흐름(order, slots, skips, corrections, reasks)은 만드는 쪽(wiring)이 정한다.
백엔드 계약(ConstraintBackend: name, version, supported_features, generate)은 wiring/ports.py에 있다."""
from __future__ import annotations

import json
import re
from typing import Callable, Mapping, Sequence

from ..types.GenerationRequest import GenerationRequest

INPUT, OUTPUT = "GenerationRequest", "GeneratedText"


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


_INT = re.compile(r"\d+")


def fake_slot_value(slot_type: str, utterance: str):
    """발화에서 첫 칸의 값을 정하는 결정적 규칙. 정하지 못하면 None(기존처럼 빈 values).
    boolean: 비어 있지 않은 발화는 '아니'를 포함하면 False, 아니면 True. integer: 발화의 첫 아라비아 숫자열, 없고 '아니'나 '없'을 포함하면 0, 그 밖은 None."""
    u = (utterance or "").strip()
    if not u:
        return None
    if slot_type == "boolean":
        return "아니" not in u
    if slot_type == "integer":
        m = _INT.search(u)
        if m:
            return int(m.group())
        return 0 if ("아니" in u or "없" in u) else None
    return None


def _next_after(i: int, order: list, saved: Mapping, skips: Sequence[Mapping]) -> str:
    """order[i]부터 시작해 저장 값에 걸리는 건너뛰기를 적용한 다음 문항(없으면 END)."""
    while i < len(order) and any(r["skip_item"] == order[i] and r["slot"] in saved and saved[r["slot"]] == r["value"]
                                 and isinstance(saved[r["slot"]], bool) == isinstance(r["value"], bool) for r in skips):
        i += 1
    return order[i] if i < len(order) else "END"


def fake_extraction_for(question_id: str, order: Sequence[str], utterance: str | None = None,
                        slots: Mapping | None = None, skips: Sequence[Mapping] = (), corrections: Sequence[Mapping] = (),
                        reasks: Sequence[Mapping] = ()) -> dict:
    """question_id 다음 문항으로 가는 최소 유효 ActionExtraction. values가 비어 있으면 저장 값이 없어 건너뛰기 규칙이 적용되지 않으므로
    기대 next_item은 순서상 바로 다음 문항(마지막이면 END)이다(계약 action_rules의 기준 검사기와 일치).
    문항에 칸 정의(slots)가 있고 발화에서 값을 정할 수 있으면 첫 칸에 ANSWERED 값을 내고, 그 값에 건너뛰기(skips)를 적용한 다음 문항으로 간다."""
    order = list(order)
    i = order.index(question_id) + 1 if question_id in order else len(order)
    # 되묻기(0.7.5): reasks 항목의 문구가 발화에 있으면 REASK. next_item은 입력 question_id(계약), targets는 key 하나, 그 칸은 MISSING으로 values에 둔다.
    for r in reasks:
        if utterance and r["contains"] in utterance and r.get("at") in (None, question_id):
            return {"action": "REASK", "next_item": question_id, "reason": r.get("reason", "MISSING"), "targets": [r["key"]],
                    "values": [{"key": r["key"], "precision": "unspecified", "save": False, "status": "MISSING", "value": None}]}
    # 정정(0.7.2): corrections 항목의 문구가 발화에 있으면(at이 있으면 그 문항일 때만) CORRECT. 앞서 한 답의 칸을 CORRECTION으로 저장한다.
    # next_item은 계약 출력 규칙대로 정정한 문항(값의 문항) 바로 다음에 건너뛰기를 적용한 문항이다.
    for c in corrections:
        if utterance and c["contains"] in utterance and c.get("at") in (None, question_id):
            item = c["key"].rsplit(".", 1)[0].split("[")[0]
            if item in order:
                slot = re.sub(r"\[[^\]]*\]", "", c["key"])
                return {"action": "CORRECT", "next_item": _next_after(order.index(item) + 1, order, {slot: c["value"]}, skips),
                        "values": [{"key": c["key"], "precision": "exact", "save": True, "status": "CORRECTION", "value": c["value"]}]}
    cols = (slots or {}).get(question_id)
    value = fake_slot_value(cols[0]["type"], utterance) if cols and utterance else None
    if value is None:
        return {"action": "NEXT", "next_item": order[i] if i < len(order) else "END", "values": []}
    key = cols[0]["key"]
    return {"action": "NEXT", "next_item": _next_after(i, order, {key: value}, skips),
            "values": [{"key": key, "precision": "exact", "save": True, "status": "ANSWERED", "value": value}]}


def route_by_adapter(table: Mapping[str, Callable[[GenerationRequest], str]]) -> Callable[[GenerationRequest], str]:
    """어댑터(역할) 이름으로 그 역할의 응답기를 고른다. 한 엔진에 두 어댑터를 올리는 구조(ADR 0003)의 가짜 쪽 모습이며, 역할마다 응답기가 따로다(MR-01)."""
    return lambda req: table[req.role](req)


def make_default_responder(order: Sequence[str] | None = None, extraction: Mapping | None = None,
                           slots: Mapping | None = None, skips: Sequence[Mapping] = (), corrections: Sequence[Mapping] = (),
                           reasks: Sequence[Mapping] = ()) -> Callable[[GenerationRequest], str]:
    """LoRA-A 응답기는 extraction(고정 출력)이 있으면 그것, 아니면 order가 있으면 입력 기준 다음 문항(slots가 있으면 발화의 값 포함), 둘 다 없으면 FAKE_EXTRACTION.
    LoRA-B 응답기는 FAKE_RESPONSE_TEXT."""
    fixed = json.dumps(extraction, ensure_ascii=False) if extraction is not None else None

    def extract(req: GenerationRequest) -> str:   # LoRA-A 가짜: 자기 입력(ActionExtractionInput)의 문항만 읽는다
        if fixed is not None:
            return fixed
        inp = req.input or {}
        qid = inp.get("question_id") if order else None
        if order and isinstance(qid, str):
            utt = inp.get("user_utterance")
            return json.dumps(fake_extraction_for(qid, order, utt if isinstance(utt, str) else None, slots, skips, corrections, reasks), ensure_ascii=False)
        return json.dumps(FAKE_EXTRACTION, ensure_ascii=False)

    def respond(req: GenerationRequest) -> str:   # LoRA-B 가짜: 입력을 읽지 않는다
        return FAKE_RESPONSE_TEXT
    return route_by_adapter({"LORA_A": extract, "LORA_B": respond})


_default_responder = make_default_responder()


def run(req: GenerationRequest) -> str:
    """컴포넌트의 함수 모양: 기본 응답기로 한 번 생성한다. 설정된 인스턴스(FakeBackend)는 wiring이 만든다."""
    return _default_responder(req)
