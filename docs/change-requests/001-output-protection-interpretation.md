# 변경 요청 001: 출력 보호 규칙의 해석 확인 (헤드에게)

작성: model-runtime 에이전트, 2026-10-15. 대상: 헤드(`boundary-contracts` 소유). 경계 계약을 바꾸자는 요청이 아니라, 규칙 전달문에서 모호했던 곳을 **이 구현이 이렇게 읽었다**고 알리고 틀리면 고쳐 달라는 요청이다. 틀린 곳이 있어도 구현은 멈추지 않고 진행했다. 출처 문서는 `docs/rules.md`.

## 1. 생성기 대체 문장 조립 (HEAD 조건 (c))
규칙 5는 "문장 재료를 그대로 읽는 문장(말할 값·알릴 것은 이름·값을 그대로 넣음)"이다. 조립 순서와 재료 선택은 규칙에 없어서 아래처럼 읽었다(`fallback.py`, 순수 함수, 발화를 쓰지 않음).

1. 알릴 것(`notices`) 순서대로: GUIDE는 `guide_id`가 같은 안내 문장, RESUME·SURVEY_STOPPED·DEFERRED는 같은 키의 고정 문장, CORRECTION_ACK는 `"{question_name}: {value}"`, KEPT_PREVIOUS는 `"{question_name}: {previous_value}"`.
2. 말할 값(`say_values`): `"{question_name}: {value}"`. 값은 문자열 그대로, `{amount, unit}`은 `38.5도`처럼 붙여서, `{code, label}`은 label(없으면 code).
3. 행동: ASK_REPEAT·CALL_STAFF·FAREWELL·ASK_CONFIRM·ASK_CORRECTION_TARGET·ASK_FINISH·RISK_NOTICE·WAIT_NOTICE·ASK_STOP·STAFF_NOTICE는 `fixed_sentences`에서 같은 키의 문장. REDIRECT는 OOS_NOTICE 고정 문장 뒤에 `awaiting`의 질문. ASK_NEXT·ASK_MISSING·ASK_CLARIFY·ASK_AFTER_UNKNOWN·ASK_AFTER_REFUSAL은 대상별로 (ASK_AFTER_REFUSAL이면 거절 설명) + (슬롯 대상이면 재질문 문장, 없으면 질문 문장) + 선택지(쉼표로 이음).
4. 같은 문장이 연달아 나오면 한 번만. 문장이 하나도 없으면 상수 "죄송합니다. 다시 한 번 말씀해 주세요."(규칙에 없는 문구라 확인이 필요하다).

확인 요청: (가) 순서(알릴 것 → 값 → 행동)가 맞는지, (나) `"{이름}: {값}"` 형식과 선택지 이음 방식이 맞는지, (다) 마지막 상수를 허용하는지, (라) ASK_CONFIRM 같은 고정 문장 행동에서 대상의 질문 문장까지 읽어야 하는지.

## 2. 기계로 확인하는 생성기 규칙의 범위
규칙 1~4 가운데 코드로 확인하는 것은 두 가지뿐이다.
- 규칙 1: 응답문의 모든 숫자열이 응답 지시에 나온 숫자열이어야 한다(발화에만 있는 숫자 금지). 숫자 아닌 값이나 문항 언급은 확인하지 못한다. 한글 수사("두 번")도 확인하지 못한다.
- 규칙 4: 행동이 RISK_NOTICE이면 `fixed_sentences`의 RISK_NOTICE 문장이 응답문에 **그대로** 들어 있어야 한다. 바꿔 말하는 것을 허용해야 한다면 알려 달라. 발화를 받아 주지 않았는지는 확인하지 못한다.
- 규칙 2·3은 기계로 확인하지 않는다(프롬프트로만 지시, 보호 전 품질은 `generate_raw` 평가).
확인 요청: 이 범위로 충분한지, 더 확인해야 할 기준(예: 규칙 2의 금지 표현 목록)이 있는지.

## 3. 파서 보호의 판단
- 규칙 위반(P2~P6)도 "유효하지 않음"으로 보고 재시도한다. 재시도를 다 쓰면 **마지막 시도**의 출력을 고친다.
- 항목 하나가 고친 뒤에도 스키마를 통과하지 못하거나 항목이 객체가 아니면, 그 항목만 버리지 않고 **전체를 빈 목록**(P1)으로 한다. "깨진 항목은 다시 말씀해 주세요로 이어진다"는 취지에는 맞지만 항목 단위 처리가 의도였는지 확인이 필요하다.
- P5의 "문항 전체에 값을 붙였으면"은 `target.whole_question`이 true이고 `value`가 null이 아닌 경우로 읽었다.
- P4는 `guide_id`가 null이 아니고 입력 `guide_list`에 없으면 의도와 관계없이 OUT_OF_SCOPE로 바꾼다(규칙 문장이 의도를 제한하지 않아서).

## 4. 부수 변경 (알림)
- `serving_config`에 필수 키 `protection_policy_version`을 추가했다(현재 `protect-1`). 보호 규칙이 출력을 바꾸므로 해시에 반영하기 위해서다. `ServingConfig` 형식은 이 리포지토리 소유이지만, 매니페스트를 쓰는 model-lab이 알아야 한다.
- 프롬프트 `prompt-1`을 지우고 `prompt-2`로 바꿨다(모델은 `response_text`만 출력). 어떤 모델 패키지도 `prompt-1`을 쓰지 않았다고 가정했다.
- `docs/brief.md` 31행은 아직 "ModelOutputInvalid"를 말한다(헤드 소유 문서라 고치지 않았다).
