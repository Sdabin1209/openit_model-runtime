import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "fixtures" / "schema" / "schemas"

PARSER_INPUT = {"utterance": "머리가 아파요", "last_directive": None, "awaiting_questions": [],
                "question_names": [], "answered_slots": [], "guide_list": []}

DIRECTIVE = {"action": "ASK_NEXT", "targets": [], "target_pending_id": None, "awaiting": None, "say_values": [],
             "reask_reasons": [], "notices": [],
             "sentence_materials": {"question_sentences": [], "refusal_explanations": [], "reask_sentences": [],
                                    "choices": [], "guide_sentences": [], "fixed_sentences": []}}
GENERATOR_INPUT = {"directive": DIRECTIVE, "utterance": "네"}

ITEM = {"item_no": 0, "target": None, "value": None, "source_text": "x", "intent": "UNRECOGNIZED",
        "progress_op": None, "guide_id": None}
GOOD_PARSE = json.dumps({"items": [ITEM]})
# ANSWERED_INTENT인데 target·value가 null: 원래 스키마의 allOf/if/then 규칙 위반
BAD_PARSE = json.dumps({"items": [dict(ITEM, intent="ANSWERED_INTENT")]})
GOOD_GEN = json.dumps({"response_text": "알겠습니다.", "is_fallback": False}, ensure_ascii=False)
