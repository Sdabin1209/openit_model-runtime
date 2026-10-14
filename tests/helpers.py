import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "fixtures" / "schema" / "schemas"

PARSER_INPUT = {"utterance": "머리가 아파요", "last_directive": None, "awaiting_questions": [],
                "question_names": [], "answered_slots": [], "guide_list": []}


def directive(action="ASK_NEXT", **kw):
    d = {"action": action, "targets": [], "target_pending_id": None, "awaiting": None, "say_values": [],
         "reask_reasons": [], "notices": [],
         "sentence_materials": {"question_sentences": [], "refusal_explanations": [], "reask_sentences": [],
                                "choices": [], "guide_sentences": [], "fixed_sentences": []}}
    sm = kw.pop("sm", {})
    d.update(kw)
    d["sentence_materials"].update(sm)
    return d


def gen_input(action="ASK_NEXT", utterance="네", **kw):
    return {"directive": directive(action, **kw), "utterance": utterance}


GENERATOR_INPUT = gen_input()

ITEM = {"item_no": 0, "target": None, "value": None, "source_text": "x", "intent": "UNRECOGNIZED",
        "progress_op": None, "guide_id": None}
SLOT = {"question_id": "Q1", "repeat_key": "", "slot_id": "s"}
WHOLE = {"question_id": "Q1", "whole_question": True}


def item(**kw):
    return dict(ITEM, **kw)


def out(*items):
    return json.dumps({"items": list(items)}, ensure_ascii=False)


GOOD_PARSE = out(ITEM)
# ANSWERED_INTENT인데 target·value가 null: 원래 스키마의 allOf/if/then 규칙 위반이자 보호 규칙 P5 해당
BAD_PARSE = out(item(intent="ANSWERED_INTENT"))
GOOD_GEN = json.dumps({"response_text": "알겠습니다."}, ensure_ascii=False)


def cfgs_and_hashes(backend, a=None, b=None):
    """두 역할의 serving_config와 기대 해시. a, b는 역할별로 덮어쓸 최상위 키."""
    import model_runtime as mr
    cfgs = mr.fake_serving_configs(backend, LORA_A=a, LORA_B=b)
    return cfgs, {r: mr.serving_config_hash(c) for r, c in cfgs.items()}
