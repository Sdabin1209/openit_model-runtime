"""키 순서(계약 v2.0.1): xgrammar는 properties 순서대로 키를 내도록 강제한다. 순서의 정본은 계약이고 relax는 순서를 바꾸지 않는다."""
import json
import unittest

from boundary_contracts import load_schema

import model_runtime as mr
from model_runtime.schema_guard import relax
from test_anyof_constraint import REL, ORIG, examples, strip

try:
    import xgrammar as xg
except Exception:   # xgrammar(torch, transformers 필요)는 선택. scripts/xgrammar_check.py가 같은 사례를 돌린다
    xg = None


def sortd(d):
    d = dict(sorted(d.items()))
    d["values"] = [dict(sorted(i.items())) for i in d["values"]]
    return d


def rev(d):
    d = dict(reversed(list(d.items())))
    d["values"] = [dict(reversed(list(i.items()))) for i in d["values"]]
    return d


class KeyOrder(unittest.TestCase):
    def test_contract_properties_sorted(self):
        top = list(ORIG["properties"])
        self.assertEqual(top, ["action", "next_item", "note", "reason", "targets", "values"])
        self.assertEqual(list(ORIG["properties"]["values"]["items"]["properties"]), ["key", "precision", "save", "status", "value"])

    def test_relax_keeps_contract_order_in_every_variant(self):
        for v in REL["anyOf"]:
            self.assertEqual(list(v["properties"]), sorted(v["properties"]))
            for it in v["properties"]["values"]["items"]["anyOf"]:
                self.assertEqual(list(it["properties"]), ["key", "precision", "save", "status", "value"])
        flat, _ = relax(strip(ORIG), mr.DEFAULT_SUPPORTED_FEATURES)
        self.assertEqual(flat, REL)

    @unittest.skipIf(xg is None, "xgrammar 없음(scripts/xgrammar_check.py 참고)")
    def test_xgrammar_accepts_sorted_rejects_reversed(self):
        v = [chr(i) for i in range(32, 127)] + ["</s>"]
        comp = xg.GrammarCompiler(xg.TokenizerInfo(v, stop_token_ids=[len(v) - 1])).compile_json_schema(json.dumps(REL))

        def ok(d):
            m = xg.GrammarMatcher(comp)
            return bool(m.accept_string(json.dumps(d, ensure_ascii=False))) and bool(m.accept_token(len(v) - 1))
        for name, d in examples().items():
            self.assertTrue(ok(sortd(d)), name)
            self.assertFalse(ok(rev(d)), name)
