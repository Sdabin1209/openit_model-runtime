"""교체 가능한 제약 디코딩 백엔드."""
from __future__ import annotations

import json
import re
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


def load_fake_flow_spec(path: str) -> tuple[dict, list]:
    """문항 흐름 파일(객체 형식)의 선택 항목을 읽는다(0.7.1). 목록 형식이거나 항목이 없으면 ({}, []).
    `slots`: {문항ID: [{"key": "Q4.answer", "type": "boolean"|"integer"}, ...]} (첫 칸만 쓴다).
    `skips`: survey-service QuestionFlow와 같은 모양 [{"slot", "value", "skip_item"}] (op는 "=="만). 형식이 틀리면 ValueError."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}, []
    slots = data.get("slots", {})
    if not isinstance(slots, dict):
        raise ValueError("slots는 객체여야 한다")
    for item, lst in slots.items():
        if (not isinstance(lst, list) or not lst or not all(isinstance(c, dict) and isinstance(c.get("key"), str) and c.get("type") in ("boolean", "integer") for c in lst)):
            raise ValueError("slots의 각 문항은 key(문자열)와 type(boolean|integer)을 가진 칸의 비어 있지 않은 목록이어야 한다")
    skips = data.get("skips", [])
    if not isinstance(skips, list) or not all(isinstance(r, dict) and isinstance(r.get("slot"), str) and isinstance(r.get("skip_item"), str)
                                              and "value" in r and r.get("op", "==") == "==" for r in skips):
        raise ValueError("skips 항목은 slot, value, skip_item을 가져야 한다(op는 ==만)")
    return slots, list(skips)


def load_fake_corrections(path: str) -> list:
    """문항 흐름 파일(객체 형식)의 선택 항목 `corrections`를 읽는다(0.7.2). 목록 형식이거나 항목이 없으면 [].
    `corrections`: [{"contains": "발화에 들어 있는 문구", "key": "Q4.answer", "value": false, "at": "Q5"(선택: 이 question_id일 때만)}].
    key의 문항은 `order`에 있어야 한다. value는 null이 아닌 boolean|number|string. 형식이 틀리면 ValueError."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return []
    corr = data.get("corrections", [])
    order = data.get("order")
    if not isinstance(corr, list):
        raise ValueError("corrections는 목록이어야 한다")
    for c in corr:
        if not (isinstance(c, dict) and isinstance(c.get("contains"), str) and c["contains"].strip() and isinstance(c.get("key"), str)
                and isinstance(c.get("value"), (bool, int, float, str)) and (c.get("at") is None or isinstance(c["at"], str))):
            raise ValueError("corrections 항목은 contains(비어 있지 않은 문자열), key(문자열), value(boolean|number|string)를 가져야 한다")
        if not _KEY.match(c["key"]):
            raise ValueError("corrections의 key가 모델 키 문법이 아니다")
        if isinstance(order, list) and c["key"].rsplit(".", 1)[0].split("[")[0] not in order:
            raise ValueError("corrections의 key 문항이 order에 없다")
    return list(corr)


_REASK_REASONS = ("MISSING", "OUT_OF_RANGE", "UNKNOWN_UNIT", "VAGUE_QUANTITY", "RANGE_SPANS_OPTIONS", "COUNT_EXCEEDS_DAYS",
                  "OTHER_BEVERAGE", "BEVERAGE_MISSING", "MISSING_NUMBER", "UNPARSED_VALUE", "INVALID_OPTION")   # ActionExtraction 스키마 allOf의 REASK 허용 reason


def load_fake_reasks(path: str) -> list:
    """문항 흐름 파일(객체 형식)의 선택 항목 `reasks`를 읽는다(0.7.3). 목록 형식이거나 항목이 없으면 [].
    `reasks`: [{"contains": "발화에 들어 있는 문구", "key": "Q4.answer", "reason": "MISSING"(선택, 기본 MISSING), "at": "Q4"(선택: 이 question_id일 때만)}].
    문구가 발화에 있으면 매번(연속으로도) REASK를 낸다. 서비스의 되묻기 한도(REASK_LIMIT) 시험용이다. 형식이 틀리면 ValueError."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return []
    rs = data.get("reasks", [])
    order = data.get("order")
    if not isinstance(rs, list):
        raise ValueError("reasks는 목록이어야 한다")
    for r in rs:
        if not (isinstance(r, dict) and isinstance(r.get("contains"), str) and r["contains"].strip() and isinstance(r.get("key"), str)
                and r.get("reason", "MISSING") in _REASK_REASONS and (r.get("at") is None or isinstance(r["at"], str))):
            raise ValueError("reasks 항목은 contains(비어 있지 않은 문자열), key(문자열), reason(REASK 허용값, 선택)을 가져야 한다")
        if not _KEY.match(r["key"]):
            raise ValueError("reasks의 key가 모델 키 문법이 아니다")
        if isinstance(order, list) and r["key"].rsplit(".", 1)[0].split("[")[0] not in order:
            raise ValueError("reasks의 key 문항이 order에 없다")
    return list(rs)


_KEY = re.compile(r"^Q[0-9]+(\.D[0-9]{2})?(_[0-9]+)?(\[(soju|beer|spirits|makgeolli|wine)\])?\.[a-z][a-z_]*$")
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
    기대 next_item은 순서상 바로 다음 문항(마지막이면 END)이다(turn-engine v0.3.0 validate_extraction과 일치).
    문항에 칸 정의(slots)가 있고 발화에서 값을 정할 수 있으면 첫 칸에 ANSWERED 값을 내고, 그 값에 건너뛰기(skips)를 적용한 다음 문항으로 간다."""
    order = list(order)
    i = order.index(question_id) + 1 if question_id in order else len(order)
    # 되묻기(0.7.3): reasks 항목의 문구가 발화에 있으면 REASK. next_item은 입력 question_id(계약), targets는 key 하나, 그 칸은 MISSING으로 values에 둔다.
    for r in reasks:
        if utterance and r["contains"] in utterance and r.get("at") in (None, question_id):
            return {"action": "REASK", "next_item": question_id, "reason": r.get("reason", "MISSING"), "targets": [r["key"]],
                    "values": [{"key": r["key"], "precision": "unspecified", "save": False, "status": "MISSING", "value": None}]}
    # 정정(0.7.2): corrections 항목의 문구가 발화에 있으면(at이 있으면 그 문항일 때만) CORRECT. 앞서 한 답의 칸을 CORRECTION으로 저장한다.
    # next_item은 turn-engine 규칙대로 정정한 문항(값의 문항) 바로 다음에 건너뛰기를 적용한 문항이다.
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


def make_default_responder(order: Sequence[str] | None = None, extraction: Mapping | None = None,
                           slots: Mapping | None = None, skips: Sequence[Mapping] = (), corrections: Sequence[Mapping] = (),
                           reasks: Sequence[Mapping] = ()) -> Callable[[GenerationRequest], str]:
    """extraction(고정 출력)이 있으면 그것, 아니면 order가 있으면 입력 기준 다음 문항(slots가 있으면 발화의 값 포함), 둘 다 없으면 FAKE_EXTRACTION."""
    fixed = json.dumps(extraction, ensure_ascii=False) if extraction is not None else None

    def respond(req: GenerationRequest) -> str:
        if req.role != "LORA_A":
            return FAKE_RESPONSE_TEXT
        if fixed is not None:
            return fixed
        inp = req.input or {}
        qid = inp.get("question_id") if order else None
        if order and isinstance(qid, str):
            utt = inp.get("user_utterance")
            return json.dumps(fake_extraction_for(qid, order, utt if isinstance(utt, str) else None, slots, skips, corrections, reasks), ensure_ascii=False)
        return json.dumps(FAKE_EXTRACTION, ensure_ascii=False)
    return respond


_default_responder = make_default_responder()
