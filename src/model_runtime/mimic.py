"""모방 컴포넌트: `MimicExtractor`(LoRA-A 모방), `MimicResponder`(LoRA-B 모방). 데모용이다. 학습된 모델이 아니다.

- 둘은 서로를 모르고(R3), 각자 자기 입출력 스키마만 안다(R4): MimicExtractor는 `ActionExtractionInput`을 읽고 `ActionExtraction`을 내고,
  MimicResponder는 `ResponseRequest`를 읽고 `ResponseText`(문장)를 낸다.
- 결정적이다: 같은 입력(과 같은 문항 흐름·규칙표)이면 같은 출력. 난수·시간을 쓰지 않는다. 문장 고르기는 입력의 해시로 한다.
- 규칙표는 코드가 아니라 데이터 파일(`data/mimic_rules.json`, 또는 `MODEL_RUNTIME_MIMIC_RULES_FILE`)이다. 문항 순서·칸 정의는 가짜 백엔드의
  `MODEL_RUNTIME_FAKE_FLOW_FILE`과 같은 입력 형식(order, slots, skips, corrections, reasks)을 받는다.
- 의료 판단을 지어내지 않는다: 값은 환자 발화에서 읽은 예/아니오·숫자뿐이고, 모를 때는 값을 지어내지 않고 되묻거나 비운다.
  응답 문장은 절차 안내(받았다, 다시 말해 달라, 넘어간다)만 하고 임상 조언을 하지 않는다.
- 칸 정의(slots)가 없는 문항에서는 칸 키를 지어낼 수 없으므로 값을 내지 않고(빈 values) 되묻기·건너뛰기·보류도 하지 않는다.
  말머리(다른 주제로의 REDIRECT)와 다음 문항 이동만 한다."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .backends import FakeBackend, GenerationRequest, route_by_adapter

DEFAULT_RULES_PATH = Path(__file__).parent / "data" / "mimic_rules.json"
_DIGITS = re.compile(r"\d+")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_KNOWN_PLACEHOLDERS = frozenset({"topic", "known", "ask", "previous", "current", "hold", "note", "user_utterance", "question"})


def load_rules(path: str | Path | None = None) -> dict:
    """규칙표를 읽고 형태를 확인한다. 틀리면 ValueError(값은 싣지 않는다)."""
    with open(path or DEFAULT_RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)
    ex, rs = rules.get("extractor"), rules.get("responder")
    if not isinstance(ex, dict) or not isinstance(rs, dict):
        raise ValueError("규칙표에는 extractor와 responder가 있어야 한다")
    for sect, names in (("redirect", ("ASK_PURPOSE", "OFF_TOPIC", "OUT_OF_SCHEMA")), ("skip", ("REQUEST_SKIP", "REFUSED"))):
        if not isinstance(ex.get(sect), dict) or set(ex[sect]) != set(names):
            raise ValueError(f"extractor.{sect}는 {', '.join(names)}를 가져야 한다")
        for n in names:
            _need_phrases(ex[sect][n])
    for k in ("hold", "advance", "assert", "correct_hint", "yes", "no", "vague"):
        _need_phrases(ex.get(k))
    nw = ex.get("number_words")
    if not isinstance(nw, dict) or not all(isinstance(k, str) and k and isinstance(v, int) and not isinstance(v, bool) for k, v in nw.items()):
        raise ValueError("number_words는 {낱말: 정수}여야 한다")
    if not (isinstance(ex.get("note_max_chars"), int) and ex["note_max_chars"] > 0):
        raise ValueError("note_max_chars는 양의 정수여야 한다")
    if not isinstance(rs.get("FALLBACK"), list):
        raise ValueError("responder.FALLBACK이 필요하다")
    for key, lst in rs.items():
        if key.startswith("_"):
            continue
        if not isinstance(lst, list) or not lst or not all(isinstance(t, str) and t.strip() for t in lst):
            raise ValueError("responder의 각 항목은 비어 있지 않은 문장 목록이어야 한다")
        for t in lst:
            if not set(_PLACEHOLDER.findall(t)) <= _KNOWN_PLACEHOLDERS:
                raise ValueError("responder 문장에 모르는 자리표가 있다")
    return rules


def _need_phrases(v: Any) -> None:
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        raise ValueError("낱말 목록은 비어 있지 않은 문자열 목록이어야 한다")


def _has(utt: str, phrases: Sequence[str]) -> bool:
    return any(p in utt for p in phrases)


def _slot_of(key: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", key)


def _item_of(key: str) -> str:
    return key.rsplit(".", 1)[0].split("[")[0]


def _val(key: str, value: Any, status: str) -> dict:
    saved = status in ("ANSWERED", "CORRECTION")
    return {"key": key, "precision": "exact" if saved else "unspecified", "save": saved, "status": status, "value": value if (saved or status == "CONFLICT") else None}


class MimicExtractor:
    """LoRA-A 모방. 입력 ActionExtractionInput -> 출력 ActionExtraction(dict).

    flow: `order`(문항 ID 목록), `slots`({문항: [{"key","type": boolean|integer}]}), `skips`([{"slot","value","skip_item"}]),
    `corrections`, `reasks`(가짜 백엔드와 같은 항목). 규칙의 순서(먼저 맞는 것):
    reasks 설정 > corrections 설정 > REDIRECT(목적 질문·딴소리·문진표 밖) > SKIP(거절·넘기기 요청) > HOLD(모름) > 다음 문항 이동 요청
    > 칸 값 읽기(CONFIRM: 앞서 저장한 값과 다름, CORRECT: 정정 말머리, REASK: 값을 못 읽음, NEXT: 값 채움)."""

    def __init__(self, order: Sequence[str] | None = None, slots: Mapping | None = None, skips: Sequence[Mapping] = (),
                 corrections: Sequence[Mapping] = (), reasks: Sequence[Mapping] = (), rules: Mapping | None = None):
        self.order = list(order) if order else []
        self.slots = dict(slots or {})
        self.skips = list(skips)
        self.corrections = list(corrections)
        self.reasks = list(reasks)
        self.rules = (rules or load_rules())["extractor"]

    # --- 문항 흐름(turn-engine의 규칙과 같은 뜻: 이 말에서 저장한 값만으로 건너뛰기를 적용한다) ---
    def _next_after(self, item: str, saved: Mapping) -> str:
        if item not in self.order:
            return "END"
        i = self.order.index(item) + 1
        while i < len(self.order) and any(
                r["skip_item"] == self.order[i] and r["slot"] in saved and saved[r["slot"]] == r["value"]
                and isinstance(saved[r["slot"]], bool) == isinstance(r["value"], bool) for r in self.skips):
            i += 1
        return self.order[i] if i < len(self.order) else "END"

    def extract(self, input: Mapping) -> dict:
        qid, utt, state = input["question_id"], input["user_utterance"].strip(), input.get("state") or {}
        r = self.rules
        if not self.order or qid not in self.order:
            return {"action": "NEXT", "next_item": "END", "values": []}   # 흐름을 모르면 아무것도 지어내지 않는다
        cols = self.slots.get(qid) or []
        for x in self.reasks:
            if x["contains"] in utt and x.get("at") in (None, qid):
                return self._reask(qid, [x["key"]], x.get("reason", "MISSING"))
        for c in self.corrections:
            if c["contains"] in utt and c.get("at") in (None, qid) and _item_of(c["key"]) in self.order:
                return self._correct(c["key"], c["value"])
        for reason, phrases in r["redirect"].items():
            if _has(utt, phrases):
                out = {"action": "REDIRECT", "next_item": qid, "reason": reason, "values": []}
                if reason == "OUT_OF_SCHEMA":   # 환자가 한 말을 그대로 줄여 쓴다(해석·진단을 덧붙이지 않는다)
                    out["note"] = utt[: r["note_max_chars"]]
                return out
        target = self._target_col(cols, state)
        if cols:
            for reason, status in (("REFUSED", "REJECTED"), ("REQUEST_SKIP", "MISSING")):
                if _has(utt, r["skip"][reason]):
                    return {"action": "SKIP", "next_item": self._next_after(qid, {}), "reason": reason, "targets": [target["key"]],
                            "values": [_val(target["key"], None, status)]}
            if _has(utt, r["hold"]):
                return {"action": "HOLD", "next_item": self._next_after(qid, {}), "reason": "UNCERTAIN", "targets": [target["key"]],
                        "values": [_val(target["key"], None, "UNCLEAR")]}
        if not cols or _has(utt, r["advance"]):
            return {"action": "NEXT", "next_item": self._next_after(qid, {}), "values": []}
        return self._fill(qid, utt, state, cols, target)

    @staticmethod
    def _target_col(cols: Sequence[Mapping], state: Mapping) -> Mapping:
        """아직 저장하지 못한 첫 칸. 모두 저장돼 있으면 첫 칸(새로 말한 값을 읽는다)."""
        done = {k for k, v in state.items() if isinstance(v, Mapping) and v.get("status") in ("ANSWERED", "CORRECTION")}
        return next((c for c in cols if c["key"] not in done), cols[0]) if cols else {}

    def _read(self, ctype: str, utt: str):
        """발화에서 칸 값을 읽는다. 읽지 못하면 (None, 이유). boolean은 아니오 낱말이 먼저, integer는 아라비아 숫자, 낱말 숫자, 없음(0) 순이다."""
        r = self.rules
        if ctype == "boolean":
            if _has(utt, r["no"]):
                return False, None
            if _has(utt, r["yes"]):
                return True, None
            return None, "MISSING"
        m = _DIGITS.search(utt)
        if m:
            return int(m.group()), None
        for w, n in r["number_words"].items():
            if w in utt:
                return n, None
        if _has(utt, r["no"]):
            return 0, None
        return None, ("VAGUE_QUANTITY" if _has(utt, r["vague"]) else "MISSING_NUMBER")

    def _fill(self, qid: str, utt: str, state: Mapping, cols: Sequence[Mapping], target: Mapping) -> dict:
        value, why = self._read(target["type"], utt)
        key = target["key"]
        if why is not None:
            return self._reask(qid, [key], why)
        prev = state.get(key)
        has_prev = isinstance(prev, Mapping) and prev.get("status") in ("ANSWERED", "CORRECTION")
        if has_prev and prev.get("value") != value:
            if _has(utt, self.rules["assert"]) or _has(utt, self.rules["correct_hint"]):   # 바꾸겠다고 분명히 말함: 정정
                return self._correct(key, value)
            return {"action": "CONFIRM", "next_item": qid, "reason": "CONFLICT_WITH_PREVIOUS", "targets": [key],
                    "values": [_val(key, value, "CONFLICT")]}
        if not has_prev and _has(utt, self.rules["correct_hint"]):   # 앞서 저장한 다른 칸을 정정하려는 말
            c = self._last_saved_like(state, value)
            if c is not None:
                return self._correct(c, value)
        out_values = [_val(key, value, "ANSWERED")]
        done = {k for k, v in state.items() if isinstance(v, Mapping) and v.get("status") in ("ANSWERED", "CORRECTION")}
        rest = [c["key"] for c in cols if c["key"] != key and c["key"] not in done]
        if rest:   # 같은 문항에 비어 있는 칸이 남음: 저장한 값은 두고 나머지를 되묻는다
            out_values += [_val(k, None, "MISSING") for k in rest]
            return {"action": "REASK", "next_item": qid, "reason": "MISSING", "targets": rest, "values": out_values}
        return {"action": "NEXT", "next_item": self._next_after(qid, {_slot_of(key): value}), "values": out_values}

    def _last_saved_like(self, state: Mapping, value: Any) -> str | None:
        """정정 대상: state에서 가장 나중에 저장한 칸 중 값의 종류(불리언/숫자)가 같고 문항이 흐름에 있는 것."""
        want_bool = isinstance(value, bool)
        for k in reversed(list(state)):
            v = state[k]
            if (isinstance(v, Mapping) and v.get("status") in ("ANSWERED", "CORRECTION") and isinstance(v.get("value"), bool) == want_bool
                    and isinstance(v.get("value"), (bool, int, float)) and _item_of(k) in self.order):
                return k
        return None

    def _correct(self, key: str, value: Any) -> dict:
        return {"action": "CORRECT", "next_item": self._next_after(_item_of(key), {_slot_of(key): value}), "values": [_val(key, value, "CORRECTION")]}

    @staticmethod
    def _reask(qid: str, targets: list, reason: str) -> dict:
        return {"action": "REASK", "next_item": qid, "reason": reason, "targets": targets, "values": [_val(k, None, "MISSING") for k in targets]}

    def generate(self, req: GenerationRequest) -> str:
        if req.input is None:
            raise ValueError("MimicExtractor는 모델 입력 객체가 필요하다")
        return json.dumps(self.extract(req.input), ensure_ascii=False, sort_keys=True)   # 키 순서는 사전순(ActionExtraction 계약)


class MimicResponder:
    """LoRA-B 모방. 입력 ResponseRequest -> 출력 ResponseText(문장). 규칙표의 문장 틀에서 고른다."""

    def __init__(self, rules: Mapping | None = None):
        self.rules = (rules or load_rules())["responder"]

    @staticmethod
    def _fields(input: Mapping) -> dict:
        nxt = input.get("next") or {}
        f = {k: input.get(k) or "" for k in ("topic", "known", "ask", "previous", "current", "hold", "note", "user_utterance")}
        f["question"] = nxt.get("text", "") if nxt.get("type") != "END" else ""
        return f

    def _candidates(self, input: Mapping) -> list[str]:
        action, reason = input["action"], input.get("reason")
        end = (input.get("next") or {}).get("type") == "END"
        keys = ([f"{action}/END"] if end else []) + ([f"{action}/{reason}"] if reason else []) + [action, "FALLBACK"]
        f = self._fields(input)
        for k in keys:
            usable = [t for t in self.rules.get(k, []) if all(f[p] for p in _PLACEHOLDER.findall(t))]
            if usable:
                return usable
        return ["알겠습니다."]

    def respond(self, input: Mapping) -> str:
        cands = self._candidates(input)
        h = hashlib.sha256(json.dumps(input, ensure_ascii=False, sort_keys=True).encode("utf-8")).digest()   # 같은 입력이면 같은 문장
        t = cands[int.from_bytes(h[:4], "big") % len(cands)]
        f = self._fields(input)
        return _PLACEHOLDER.sub(lambda m: f[m.group(1)], t).strip()

    def generate(self, req: GenerationRequest) -> str:
        if req.input is None:
            raise ValueError("MimicResponder는 모델 입력 객체가 필요하다")
        return self.respond(req.input)


class MimicBackend(FakeBackend):
    """모방 컴포넌트 두 개를 한 백엔드로 묶는다. 이름이 `mimic`이라 `/v1/info`와 serving_config.constraint.backend에 그대로 보인다.
    가짜 백엔드와 같은 요청 기록·특성 집합을 쓰고, 어댑터 이름으로 모방 컴포넌트를 고른다(ADR 0003의 한 엔진·두 어댑터 모양)."""
    name = "mimic"
    version = "1"

    def __init__(self, extractor: MimicExtractor | None = None, responder: MimicResponder | None = None, **kw):
        self.extractor = extractor or MimicExtractor()
        self.responder = responder or MimicResponder()
        super().__init__(route_by_adapter({"LORA_A": self.extractor.generate, "LORA_B": self.responder.generate}), **kw)


def mimic_backend_from_files(flow_file: str | None = None, rules_file: str | None = None) -> MimicBackend:
    """문항 흐름 파일(가짜 백엔드와 같은 형식)과 규칙표 파일(선택)로 MimicBackend를 만든다. 틀리면 OSError/ValueError."""
    from .backends import load_fake_corrections, load_fake_flow, load_fake_flow_spec, load_fake_reasks
    rules = load_rules(rules_file)
    if not flow_file:
        return MimicBackend(MimicExtractor(rules=rules), MimicResponder(rules))
    order = load_fake_flow(flow_file)
    slots, skips = load_fake_flow_spec(flow_file)
    return MimicBackend(MimicExtractor(order, slots, skips, load_fake_corrections(flow_file), load_fake_reasks(flow_file), rules), MimicResponder(rules))
