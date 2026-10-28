"""D83(relax-3): allOf의 if/then(/else)를 같은 뜻의 anyOf로 펼치는 변환의 등가 시험(CPU, jsonschema만).
원본 계약 스키마(boundary_contracts)에서 기계적으로 만든 변환 스키마가 원본과 같은 판정을 내는지 본다."""
import copy
import itertools
import json
import unittest
from pathlib import Path

import jsonschema
from boundary_contracts import load_schema

import model_runtime as mr
from model_runtime.schema_guard import MAX_VARIANTS, relax

# 왜: 예제는 설치된 계약 패키지(태그)의 conformance/examples를 쓴다. 형제 폴더 없이도 같은 태그의 예제로 돈다(새 머신).
import conformance
EXAMPLES = Path(conformance.__file__).resolve().parent / "examples"
FEATS = frozenset(mr.DEFAULT_SUPPORTED_FEATURES)
ORIG = load_schema("ActionExtraction")
V = lambda s: jsonschema.Draft202012Validator(s)


def strip(n):
    if isinstance(n, dict):
        return {k: strip(v) for k, v in n.items() if k != "$comment"}
    return [strip(v) for v in n] if isinstance(n, list) else n


ORIG_V = V(ORIG)
REL, REMOVED = relax(strip(ORIG), FEATS)                                  # 기본 지원 집합(maxItems 포함)
REL_V = V(REL)
EXACT, EXACT_REMOVED = REL, REMOVED
EXACT_V = REL_V
NOMAX, NOMAX_REMOVED = relax(strip(ORIG), FEATS - {"maxItems"})            # maxItems를 모르는 엔진
NOMAX_V = V(NOMAX)

REASONS = [None] + ORIG["properties"]["reason"]["enum"]
ACTIONS = ORIG["properties"]["action"]["enum"]
OK_ITEM = {"key": "Q1.D01.diagnosed", "value": True, "precision": "exact", "status": "ANSWERED", "save": True}


def examples():
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES.glob("ActionExtraction.*.json"))
            if p.name.count(".") == 2}


def top_cases():
    for a, r, t, n, vals in itertools.product(
            ACTIONS, REASONS, (None, ["Q1.D01.diagnosed"]), (None, "요약"),
            ([], [OK_ITEM], [dict(OK_ITEM, save=False)])):
        d = {"action": a, "next_item": "END", "values": vals}
        if r is not None: d["reason"] = r
        if t is not None: d["targets"] = t
        if n is not None: d["note"] = n
        yield d


def item_cases():
    for st, pr, sv, val in itertools.product(ORIG["properties"]["values"]["items"]["properties"]["status"]["enum"],
                                             ("exact", "approximate", "unspecified"), (True, False),
                                             (None, True, 3, "a")):
        yield {"action": "NEXT", "next_item": "END",
               "values": [{"key": "Q1.D01.diagnosed", "value": val, "precision": pr, "status": st, "save": sv}]}


def mutations():
    """유효한 예시를 규칙 하나씩 어기게 만든 사례. (이름, 사례)."""
    ex = examples()
    out = []

    def m(name, base, fn):
        d = copy.deepcopy(ex[base]); fn(d); out.append((name, d))
    m("NEXT에 reason", "ActionExtraction.next.json", lambda d: d.update(reason="MISSING"))
    m("NEXT에 targets", "ActionExtraction.next.json", lambda d: d.update(targets=["Q10.days"]))
    m("NEXT에 note", "ActionExtraction.next.json", lambda d: d.update(note="x"))
    m("CORRECT에 reason", "ActionExtraction.next.json", lambda d: d.update(action="CORRECT", reason="MISSING"))
    m("REASK reason 없음", "ActionExtraction.reask.json", lambda d: d.pop("reason"))
    m("REASK targets 없음", "ActionExtraction.reask.json", lambda d: d.pop("targets"))
    m("REASK에 CONFIRM의 reason", "ActionExtraction.reask.json", lambda d: d.update(reason="CONFLICT_WITH_PREVIOUS"))
    m("REASK에 UNCERTAIN", "ActionExtraction.reask.json", lambda d: d.update(reason="UNCERTAIN"))
    m("REASK에 note", "ActionExtraction.reask.json", lambda d: d.update(note="x"))
    m("CONFIRM에 REASK의 reason", "ActionExtraction.confirm.json", lambda d: d.update(reason="MISSING"))
    m("CONFIRM reason 없음", "ActionExtraction.confirm.json", lambda d: d.pop("reason"))
    m("HOLD에 REFUSED", "ActionExtraction.hold.json", lambda d: d.update(reason="REFUSED"))
    m("HOLD targets 없음", "ActionExtraction.hold.json", lambda d: d.pop("targets"))
    m("SKIP 신규 허용 reason", "ActionExtraction.hold.json", lambda d: d.update(action="SKIP", reason="REFUSED"))
    m("SKIP에 UNCERTAIN", "ActionExtraction.hold.json", lambda d: d.update(action="SKIP"))
    m("REDIRECT reason 없음", "ActionExtraction.redirect.json", lambda d: d.pop("reason"))
    m("REDIRECT에 targets", "ActionExtraction.redirect.json", lambda d: d.update(targets=["Q1.D01.diagnosed"]))
    m("REDIRECT에 잘못된 reason", "ActionExtraction.redirect.json", lambda d: d.update(reason="MISSING"))
    m("REDIRECT values 비어 있지 않음", "ActionExtraction.redirect.json", lambda d: d.update(values=[OK_ITEM]))
    m("OUT_OF_SCHEMA인데 note 없음", "ActionExtraction.redirect.json", lambda d: (d.update(reason="OUT_OF_SCHEMA"), d.pop("note", None)))
    m("OUT_OF_SCHEMA 아닌데 note", "ActionExtraction.redirect.json", lambda d: d.update(reason="OFF_TOPIC", note="x"))
    m("ANSWERED save false", "ActionExtraction.next.json", lambda d: d["values"][0].update(save=False))
    m("ANSWERED precision unspecified", "ActionExtraction.next.json", lambda d: d["values"][0].update(precision="unspecified"))
    m("ANSWERED value null", "ActionExtraction.next.json", lambda d: d["values"][0].update(value=None))
    m("CORRECTION save false", "ActionExtraction.next.json", lambda d: d["values"][0].update(status="CORRECTION", save=False))
    m("MISSING value 비null", "ActionExtraction.hold.json", lambda d: d["values"][0].update(status="MISSING", value=3))
    m("UNKNOWN precision exact", "ActionExtraction.hold.json", lambda d: d["values"][0].update(precision="exact"))
    m("CONFLICT save true", "ActionExtraction.confirm.json", lambda d: d["values"][0].update(save=True))
    m("REJECTED save true", "ActionExtraction.hold.json", lambda d: d["values"][0].update(status="REJECTED", save=True))
    m("UNCLEAR save true", "ActionExtraction.hold.json", lambda d: d["values"][0].update(status="UNCLEAR", save=True))
    m("action 없음", "ActionExtraction.next.json", lambda d: d.pop("action"))
    m("알 수 없는 action", "ActionExtraction.next.json", lambda d: d.update(action="START"))
    m("추가 키", "ActionExtraction.next.json", lambda d: d.update(extra=1))
    return out


class Equivalence(unittest.TestCase):
    def test_removed_record_is_exact(self):
        self.assertIn("maxItems", FEATS)
        self.assertEqual(REMOVED, ["allOf->anyOf"])
        self.assertEqual(NOMAX_REMOVED, ["allOf->anyOf", "maxItems"])
        for kw in ("'allOf'", "'if'", "'then'", "'else'", "'oneOf'", "'not'"):
            self.assertNotIn(kw, repr(REL))

    def test_valid_examples_accepted(self):
        ex = examples()
        self.assertEqual(len(ex), 5)
        for name, d in ex.items():
            self.assertTrue(ORIG_V.is_valid(d), name)
            self.assertTrue(REL_V.is_valid(d), name)

    def test_mutations_same_verdict(self):
        muts = mutations()
        self.assertGreaterEqual(len(muts), 30)
        rejected = 0
        for name, d in muts:
            o = ORIG_V.is_valid(d)
            self.assertEqual(o, EXACT_V.is_valid(d), name)
            if name != "REDIRECT values 비어 있지 않음":     # maxItems를 모르는 엔진에서만 넓어지는 한 곳(기록됨)
                self.assertEqual(o, NOMAX_V.is_valid(d), name)
            rejected += not o
        self.assertGreaterEqual(rejected, 29)

    def test_exhaustive_top_level(self):
        n = valid = 0
        for d in top_cases():
            o = ORIG_V.is_valid(d)
            self.assertEqual(o, EXACT_V.is_valid(d), d)
            if o:
                self.assertTrue(REL_V.is_valid(d), d)         # 좁히지 않는다
            valid += o
            n += 1
        self.assertGreater(valid, 20)
        self.assertEqual(n, 7 * 21 * 2 * 2 * 3)

    def test_exhaustive_value_items(self):
        n = valid = 0
        for d in item_cases():
            o = ORIG_V.is_valid(d)
            self.assertEqual(o, REL_V.is_valid(d), d)
            valid += o
            n += 1
        self.assertEqual(n, 7 * 3 * 2 * 4)
        self.assertGreater(valid, 5)

    def test_without_maxitems_only_redirect_values_widen(self):
        d = copy.deepcopy(examples()["ActionExtraction.redirect.json"]); d["values"] = [OK_ITEM]
        self.assertFalse(ORIG_V.is_valid(d)); self.assertFalse(REL_V.is_valid(d)); self.assertTrue(NOMAX_V.is_valid(d))

    def test_variant_shape(self):
        self.assertEqual(len(REL["anyOf"]), 8)       # NEXT, CORRECT, REASK, CONFIRM, HOLD, SKIP, REDIRECT(+note), REDIRECT(-note)
        items = REL["anyOf"][0]["properties"]["values"]["items"]["anyOf"]
        self.assertEqual(len(items), 3)              # ANSWERED/CORRECTION, MISSING/UNCLEAR/UNKNOWN/REJECTED, CONFLICT


class Limits(unittest.TestCase):
    def test_unsupported_shapes_recorded_not_silent(self):
        s = {"type": "object", "properties": {"a": {"enum": [1, 2]}, "b": {"type": "string"}, "c": {"type": "string"}},
             "allOf": [{"if": {"properties": {"a": {"minimum": 1}}}, "then": {"required": ["b"]}},        # const/enum 아님
                       {"if": {"properties": {"b": {"const": "x"}}}, "then": {"required": ["c"]}},        # b에 유한 값 집합이 없다
                       {"if": {"properties": {"a": {"const": 1}}, "required": ["a"]}, "then": {"required": ["b"]}}]}
        r, removed = relax(s, FEATS)
        self.assertEqual(removed, ["allOf->anyOf", "allOf@/[0]:if is not const/enum", "allOf@/[1]:no finite domain for b"])
        v = V(r)
        self.assertTrue(v.is_valid({"a": 1, "b": "s"}))
        self.assertFalse(v.is_valid({"a": 1}))
        self.assertTrue(v.is_valid({"a": 2}))

    def test_explosion_capped_and_recorded(self):
        n = 8
        props = {f"p{i}": {"enum": [0, 1]} for i in range(n)}
        props.update({f"q{i}": {"type": "string"} for i in range(n)})
        s = {"type": "object", "properties": props,
             "allOf": [{"if": {"properties": {f"p{i}": {"const": 1}}}, "then": {"required": [f"q{i}"]}} for i in range(n)]}
        r, removed = relax(s, FEATS)
        self.assertNotIn("anyOf", r); self.assertNotIn("allOf", r)
        self.assertEqual(removed, [f"allOf@/:variants>{MAX_VARIANTS}"])
        s["allOf"] = s["allOf"][:5]                           # 2^5 = 32 <= 64
        self.assertEqual(len(relax(s, FEATS)[0]["anyOf"]), 32)

    def test_no_anyof_support_drops_as_before(self):
        r, removed = relax(strip(ORIG), ["type", "properties", "required", "enum"])
        self.assertIn("allOf", removed); self.assertNotIn("anyOf", r)

    def test_allof_in_features_untouched(self):
        s = {"type": "object", "allOf": [{"required": ["a"]}]}
        self.assertEqual(relax(s, ["allOf", "required", "type"]), (s, []))

    def test_pure(self):
        a = strip(ORIG); b = copy.deepcopy(a)
        self.assertEqual(relax(a, FEATS), relax(a, FEATS)); self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
