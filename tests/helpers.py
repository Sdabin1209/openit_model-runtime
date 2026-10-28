import json
import time
from pathlib import Path

from boundary_contracts import validator

SCHEMA_DIR = Path(__file__).parent / "fixtures" / "schema" / "schemas"

# 현행 계약의 예시(boundary-contracts/examples)와 같은 모양의 시험 입력. 모두 test_runtime의 Samples가 패키지 스키마로 검증한다.
A_INPUT = {"question_id": "Q1.D03", "current_question": "다음은 고혈압입니다. 진단받은 적이 있으신지, 지금 약을 드시는지 알려 주세요.",
           "user_utterance": "십 년 전에 고혈압 진단을 받았어요.",
           "state": {"Q1.D02.diagnosed": {"value": False, "status": "ANSWERED"}}}
B_INPUT = {"action": "REASK", "reason": "MISSING", "topic": "고혈압 진단을 받았는지와 지금 약을 먹는지",
           "user_utterance": "십 년 전에 고혈압 진단을 받았어요.", "known": "진단 받음", "ask": "지금 고혈압 약을 먹는지",
           "next": {"type": "SAME", "question_id": "Q1.D03", "text": "다음은 고혈압입니다. 진단받은 적이 있으신지, 지금 약을 드시는지 알려 주세요."}}
B_START = {"action": "START", "topic": "뇌졸중(중풍) 진단을 받았는지와 지금 약을 먹는지",
           "next": {"type": "NEXT", "question_id": "Q1.D01", "text": "혹시 뇌졸중으로 진단받으셨거나 현재 약물 치료 중이신가요?"}}
B_END = {"action": "NEXT", "topic": "최근 1주일 동안 근력 운동을 한 날 수", "user_utterance": "근력 운동하는 날은 이틀이야",
         "known": "최근 일주일 2일", "next": {"type": "END"}}

GOOD_EXTRACTION = {"action": "REASK", "next_item": "Q1.D03", "reason": "MISSING", "targets": ["Q1.D03.on_medication"],
                   "values": [{"key": "Q1.D03.diagnosed", "value": True, "precision": "exact", "status": "ANSWERED", "save": True},
                              {"key": "Q1.D03.on_medication", "value": None, "precision": "unspecified", "status": "MISSING", "save": False}]}
GOOD_A = json.dumps(GOOD_EXTRACTION, ensure_ascii=False)
# 스키마 위반 두 가지: ANSWERED인데 save가 false(allOf/if/then 규칙, 제약 디코딩이 놓칠 수 있다), reason이 NEXT에 붙음
BAD_A = json.dumps({**GOOD_EXTRACTION, "values": [{**GOOD_EXTRACTION["values"][0], "save": False}]}, ensure_ascii=False)
GOOD_B = "십 년 전에 고혈압 진단을 받으셨군요. 지금 약은 드시고 계세요?"


def assert_valid(name, value):
    errs = list(validator(name).iter_errors(value))
    assert not errs, (name, [e.message for e in errs][:3])


def cfgs_and_hashes(backend, a=None, b=None):
    """두 역할의 serving_config와 기대 해시. a, b는 역할별로 덮어쓸 최상위 키."""
    import model_runtime as mr
    cfgs = mr.fake_serving_configs(backend, LORA_A=a, LORA_B=b)
    return cfgs, {r: mr.serving_config_hash(c) for r, c in cfgs.items()}


def wait_until(pred, timeout=5.0):
    """고정 sleep 대신 조건이 참이 될 때까지 기다린다(상한 안에서 짧게 폴링). 마지막 판정을 돌려준다."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()
