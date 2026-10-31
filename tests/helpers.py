import json
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


# 3.0.0: extract는 항상 흐름표(flow)와 칸 규격(fields)과 함께 부른다. A_INPUT(Q1.D03)용 최소 규격: 이 문항이 마지막이라 NEXT의 다음 문항은 END다.
A_FLOW = {"order": ["Q1.D03"], "skips": []}
A_FIELDS = {"fields": [{"slot": "Q1.D03.diagnosed", "data_type": "boolean"}]}


def rules_for(inp, flow=None):
    """입력 한 건을 위한 (flow, fields). flow가 없으면 입력의 문항이 마지막인 한 문항 흐름(A_INPUT 같은 단독 시험), 칸 규격은 그 문항의 칸 하나다."""
    flow = flow or ({"order": [inp.get("question_id", "Q1.D03")], "skips": []})
    return flow, {"fields": [{"slot": inp.get("question_id", "Q1.D03") + ".answer", "data_type": "boolean"}]} if inp.get("question_id", "Q1.D03") != "Q1.D03" else A_FIELDS


def extract_body(inp, flow=None):
    """HTTP extract 요청 본문(ExtractRequest)."""
    f, fl = rules_for(inp, flow)
    return {"input": inp, "flow": f, "fields": fl}


def assert_valid(name, value):
    errs = list(validator(name).iter_errors(value))
    assert not errs, (name, [e.message for e in errs][:3])


def cfgs_and_hashes(backend, a=None, b=None):
    """두 역할의 serving_config와 기대 해시. a, b는 역할별로 덮어쓸 최상위 키."""
    import model_runtime as mr
    cfgs = mr.fake_serving_configs(backend, LORA_A=a, LORA_B=b)
    return cfgs, {r: mr.serving_config_hash(c) for r, c in cfgs.items()}


from fake_clock import HANG_GUARD_SECONDS, _TICK   # 매달림 감시 마감 하나(근거는 fake_clock.py). 시험마다 숫자를 쓰지 않는다
HANG_GUARD_S = HANG_GUARD_SECONDS


def wait_until(pred, guard=HANG_GUARD_SECONDS):
    """고정 sleep 대신 조건이 참이 될 때까지 기다린다. 조건이 참이 되면 바로 돌아온다. guard는 매달림 감시(조건이 영영 안 올 때 시험이 멈추게)이며 판정이 아니다.
    폴링 횟수로 세므로 실제 시계를 읽지 않는다(5ms 간격은 폴링 간격일 뿐이다)."""
    for _ in range(int(guard / 0.005)):
        if pred():
            return True
        _TICK.wait(0.005)
    return pred()


def question_flow_table():
    """시험용 문항 흐름표(QuestionFlowTable 모양: order, skips). 정본은 문진 스키마의 data/question_flow.json이고 사본을 두지 않는다(ADR 0021).
    찾는 순서: 설치된 survey_schema 패키지 -> 환경 변수 SURVEY_SCHEMA_DIR -> 상위 폴더들의 survey-service/schema. 없으면 건너뛴다(읽을 수 없는 것을 있는 척하지 않는다)."""
    import os
    import pytest
    flow = None
    try:
        from survey_schema import load_data
        flow = load_data("question_flow")
    except Exception:   # noqa: BLE001 (미설치 또는 데이터 없음: 아래 경로로)
        roots = [Path(os.environ["SURVEY_SCHEMA_DIR"])] if os.environ.get("SURVEY_SCHEMA_DIR") else []
        roots += [p / "survey-service" / "schema" for p in Path(__file__).resolve().parents]
        for r in roots:
            f = r / "data" / "question_flow.json"
            if f.is_file():
                flow = json.loads(f.read_text(encoding="utf-8"))
                break
    if flow is None:
        pytest.skip("문진 스키마의 question_flow.json을 찾지 못했다(survey_schema 설치 또는 SURVEY_SCHEMA_DIR)", allow_module_level=True)
    table = {"order": list(flow["order"]), "skips": list(flow["skips"])}   # 계약 QuestionFlowTable은 order와 skips만 받는다
    assert_valid("QuestionFlowTable", table)
    return table


def rule_violations(inp, out, flow):
    """계약 기준 검사기로 본 위반 목록(Violation, .code). 출력 규칙의 기대값은 계약에서 온다(ADR 0021)."""
    from boundary_contracts.action_rules import check_action_extraction
    return check_action_extraction(inp, out, flow)


def skips_of(flow):
    """QuestionFlowTable의 skips를 fake 응답기가 받는 모양(slot, op, value, skip_item 사전)으로 돌려준다."""
    return [dict(x) for x in flow["skips"]]


class Served:
    """시험용: waitress 서버를 스레드에서 돌리고 예전 서버 객체와 같은 server_address, shutdown을 준다(제품 코드가 아니다)."""
    def __init__(self, srv):
        import threading
        self._srv = srv
        self.server_address = ("127.0.0.1", srv.effective_port)
        self._th = threading.Thread(target=srv.run, daemon=True); self._th.start()

    def shutdown(self):
        self._srv.close()


def serve_app(svc, **kw):
    """svc를 실제 진입점과 같은 설정(server.make_server)으로 띄운다. 포트는 운영체제가 배정한다."""
    from model_runtime import server
    return Served(server.make_server(svc, "127.0.0.1", 0, **kw))
