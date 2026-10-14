"""출력 보호 (D36, docs/rules.md). 순수 함수. 오류·보호 이름에 발화와 출력 원문을 담지 않는다."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectionPolicy:
    """출력 보호 정책 한 버전. 출력을 바꾸는 값과 해석은 모두 여기에 묶고, 바꾸려면 새 버전을 만든다.
    serving_config.protection_policy_version으로 기록되어 serving_config_hash에 반영된다."""
    version: str
    # 생성기 대체 문장을 만들 재료가 하나도 없을 때의 문장. 규칙에 없는 문구이며 문구 확정은 도메인 소유자 몫이다(잠정).
    empty_fallback_text: str
    # 규칙 문장에서 모호했던 곳을 어떻게 읽었는지. 다음 정책 버전에서 바꿀 때 근거가 된다(헤드 승인: change-requests/001).
    judgments: tuple


PROTECTION_POLICIES = {
    "protect-1": ProtectionPolicy(
        version="protect-1",
        empty_fallback_text="죄송합니다. 다시 한 번 말씀해 주세요.",   # 잠정
        judgments=(
            "J1 파서: 고친 뒤에도 항목 하나가 스키마를 통과하지 못하거나 항목이 객체가 아니면 그 항목만 버리지 않고 전체를 빈 목록(P1)으로 한다. 보수적이고 항목 정렬(item_no)이 깨지는 것을 막는다.",
            "J2 파서 P4: guide_id가 null이 아니고 입력 guide_list에 없으면 의도와 관계없이 OUT_OF_SCOPE로 바꾼다(규칙 문장이 의도를 제한하지 않는다).",
            "J3 파서 P5: '문항 전체에 값을 붙임'은 target.whole_question이 true이고 value가 null이 아닌 경우다.",
        ),
    ),
}
PROTECTION_POLICY_VERSION = "protect-1"   # 현재 정책. 지원하는 정책은 PROTECTION_POLICIES의 키다.

_ID_KEYS = frozenset({"question_id", "slot_id", "repeat_key", "guide_id", "target_pending_id", "key", "guide_ids"})
_CLEARED = ("target", "value", "progress_op", "guide_id")


def _is_str_in(v: Any, vocab: Iterable[str]) -> bool:
    return isinstance(v, str) and v in vocab


def _to(item: dict, intent: str) -> None:
    """의도를 바꿀 때는 대상·값·진행 조작·안내 항목 ID를 비운다."""
    item["intent"] = intent
    for k in _CLEARED:
        item[k] = None


def protect_parser(out: Any, *, intents: Iterable[str], progress_ops: Iterable[str], guide_ids: Iterable[str],
                   schema_ok: Callable[[dict], bool]) -> tuple[dict, dict[str, int]]:
    """파서 출력에 대체 출력 규칙(P1~P6)을 적용한다. 돌려주는 것: (규칙에 맞는 출력, {규칙 이름: 건수}).
    건수가 비어 있으면 고칠 것이 없었다. 대상이 문항 정의에 없는 항목은 고치지 않는다."""
    intents, progress_ops, guide_ids = tuple(intents), tuple(progress_ops), frozenset(guide_ids)
    empty = ({"items": []}, {"P1": 1})
    if not isinstance(out, dict) or set(out) != {"items"} or not isinstance(out["items"], list):
        return empty
    hits: Counter = Counter()
    items = []
    for raw in out["items"]:
        if not isinstance(raw, dict):
            return empty
        it = dict(raw)
        if not _is_str_in(it.get("intent"), intents):                                    # P2
            _to(it, "UNRECOGNIZED"); hits["P2"] += 1
        po = it.get("progress_op")
        if (po is not None and not _is_str_in(po, progress_ops)) or (it["intent"] == "PROGRESS_OP" and po is None):
            _to(it, "UNRECOGNIZED"); hits["P3"] += 1                                     # P3
        gid = it.get("guide_id")
        if (gid is not None and not _is_str_in(gid, guide_ids)) or (it["intent"] == "GUIDE_REQUEST" and gid is None):
            _to(it, "OUT_OF_SCOPE"); hits["P4"] += 1                                     # P4
        target, value, intent = it.get("target"), it.get("value"), it["intent"]
        whole = isinstance(target, dict) and target.get("whole_question") is True
        if ((intent in ("ANSWERED_INTENT", "SAYS_UNKNOWN", "SAYS_REFUSAL") and target is None)
                or (intent == "ANSWERED_INTENT" and value is None)
                or (intent in ("ANSWERED_INTENT", "CORRECTION_MARKED") and whole and value is not None)):
            _to(it, "UNRECOGNIZED"); hits["P5"] += 1                                     # P5
        elif intent == "CORRECTION_MARKED" and value is not None and target is None:
            it["value"] = None; hits["P6"] += 1                                          # P6
        items.append(it)
    result = {"items": items}
    if not schema_ok(result):
        return empty
    return result, dict(hits)


# --- 생성기 ----------------------------------------------------------------

def num_text(x: float | int) -> str:
    return str(int(x)) if float(x) == int(x) else repr(float(x))


def value_text(v: Any) -> str:
    """Value(문자열, {amount, unit}, {code, label})를 이름·값 그대로 읽을 문자열로."""
    if isinstance(v, str):
        return v
    if isinstance(v, Mapping):
        if "amount" in v:
            return f"{num_text(v['amount'])}{v.get('unit', '')}"
        return str(v.get("label") or v.get("code") or "")
    return ""


def _texts(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        yield num_text(node)
    elif isinstance(node, Mapping):
        for k, v in node.items():
            if k not in _ID_KEYS:
                yield from _texts(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _texts(v)


def generator_rule_errors(response_text: str, generator_input: Mapping) -> list[str]:
    """기계로 확인할 수 있는 규칙(G1, G4)만 확인한다. G2, G3은 확인하지 못한다(docs/rules.md)."""
    directive = generator_input["directive"]
    errors = []
    allowed = {run for t in _texts(directive) for run in re.findall(r"\d+", t)}
    if any(run not in allowed for run in re.findall(r"\d+", response_text)):
        errors.append("rule:G1")
    if directive["action"] == "RISK_NOTICE":
        needed = [f["sentence"] for f in directive["sentence_materials"]["fixed_sentences"] if f["key"] == "RISK_NOTICE"]
        if any(s not in response_text for s in needed):
            errors.append("rule:G4")
    return errors
