"""생성기 대체 문장: 응답 지시의 문장 재료를 그대로 읽는다. 순수 함수이고 발화를 쓰지 않는다.

조립 순서와 재료 해석은 이 리포지토리의 해석이다(docs/change-requests/001-output-protection-interpretation.md)."""
from __future__ import annotations

from typing import Any, Mapping

from .protection import value_text

DEFAULT_FALLBACK_TEXT = "죄송합니다. 다시 한 번 말씀해 주세요."
_FIXED_ACTIONS = ("ASK_REPEAT", "CALL_STAFF", "FAREWELL", "ASK_CONFIRM", "ASK_CORRECTION_TARGET", "ASK_FINISH",
                  "RISK_NOTICE", "WAIT_NOTICE", "ASK_STOP", "STAFF_NOTICE")
_ASK_ACTIONS = ("ASK_NEXT", "ASK_MISSING", "ASK_CLARIFY", "ASK_AFTER_UNKNOWN", "ASK_AFTER_REFUSAL")
_NOTICE_FIXED = ("RESUME", "SURVEY_STOPPED", "DEFERRED")


def _first(pairs, key):
    for k, s in pairs:
        if k == key:
            return s
    return None


def _ask_part(d: Mapping, sm: Mapping) -> list[str]:
    """질문 문장(재질문 문장 포함), 거절 설명, 선택지를 문장 재료에서 가져온다."""
    qs = [(q["question_id"], q["sentence"]) for q in sm["question_sentences"]]
    rs = [(r["question_id"], r["sentence"]) for r in sm["refusal_explanations"]]
    reask = [(json_key(r["slot"]), r["sentence"]) for r in sm["reask_sentences"]]
    choices = {json_key(c["slot"]): c["choices"] for c in sm["choices"]}
    out: list[str] = []
    for t in d["targets"]:
        qid = t["question_id"]
        whole = t.get("whole_question") is True
        if d["action"] == "ASK_AFTER_REFUSAL" and _first(rs, qid):
            out.append(_first(rs, qid))
        sent = None if whole else _first(reask, json_key(t))
        out.append(sent or _first(qs, qid) or "")
        if not whole and json_key(t) in choices:
            out.append(", ".join(choices[json_key(t)]))
    return [s for s in out if s]


def json_key(slot: Mapping) -> tuple:
    return (slot["question_id"], slot.get("repeat_key", ""), slot.get("slot_id", ""))


def fallback_text(generator_input: Mapping) -> str:
    d: Any = generator_input["directive"]
    sm = d["sentence_materials"]
    fixed = [(f["key"], f["sentence"]) for f in sm["fixed_sentences"]]
    guides = [(g["guide_id"], g["sentence"]) for g in sm["guide_sentences"]]
    parts: list[str] = []
    for n in d["notices"]:                                                   # 알릴 것
        kind = n["kind"]
        if kind == "GUIDE":
            parts.append(_first(guides, n.get("guide_id")) or "")
        elif kind in _NOTICE_FIXED:
            parts.append(_first(fixed, kind) or "")
        elif kind in ("CORRECTION_ACK", "KEPT_PREVIOUS"):
            v = n.get("previous_value") if kind == "KEPT_PREVIOUS" else n.get("value")
            parts.append(f"{n.get('question_name') or ''}: {value_text(v)}" if v is not None else "")
    for sv in d["say_values"]:                                               # 말할 값
        parts.append(f"{sv['question_name']}: {value_text(sv['value'])}")
    action = d["action"]                                                     # 행동
    if action in _FIXED_ACTIONS:
        parts.append(_first(fixed, action) or "")
    elif action == "REDIRECT":
        parts.append(_first(fixed, "OOS_NOTICE") or "")
        if d.get("awaiting"):
            parts.extend(_ask_part(d["awaiting"], sm))
    elif action in _ASK_ACTIONS:
        parts.extend(_ask_part(d, sm))
    kept: list[str] = []
    for p in parts:
        if p and (not kept or kept[-1] != p):
            kept.append(p)
    return " ".join(kept) or DEFAULT_FALLBACK_TEXT
