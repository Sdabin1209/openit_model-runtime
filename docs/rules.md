# 출력 보호 규칙 (D36, D75) — 계약 2.0.0 기준

이 문서는 2.0.0(D74, D75)에서 다시 썼다. v1의 파서 보호(P1~P6)와 생성기 규칙(G1~G5, 위험 안내 포함)은 v1 계약이 기술하던 단계(정규화기·판정기·Plan, 위험 신호 의도)와 함께 없어졌다. v1 문서는 git 기록(0.5.3 이전)에 남아 있다.
보호 규칙의 구현과 해석은 model-runtime이 소유한다. 계약이 정한 것과 이 리포지토리가 해석한 것을 구분해서 적는다.

> 0.6.1: 이 문서의 보호 규칙은 프롬프트 템플릿 버전과 무관하다. 입력 직렬화(`prompt-4`)는 boundary.md에 있다.

## 계약이 정한 것 (INTERFACES I9, HTTP.md I9, D36, D75)

| 호출 | 형식 | 끝내 못 지키면 |
| --- | --- | --- |
| `extract`(LoRA-A) | 출력은 `ActionExtraction` 스키마를 엄격하게 통과해야 한다 | **대체 출력을 만들지 않는다**(값을 지어내지 않는다). 라이브러리는 `ModelOutputInvalid`, HTTP는 503 `UNAVAILABLE`(`details.reason=OUTPUT_INVALID`, `details.attempts`). 정상이면 `X-Openit-Outcome: ok` |
| `respond`(LoRA-B) | 출력은 문장 그대로의 텍스트(`ResponseText`: 비어 있지 않은 문자열) | 입력의 재료(`known`, `ask`, `hold`, `next.text`)를 이어 붙여 읽는 문장을 대신 내고 `is_fallback=true`(HTTP는 `X-Openit-Outcome: fallback`, 상태 200). 정상이면 `ok` |

공통: 재시도는 `max_attempts`까지, 프롬프트는 첫 시도와 같고 시드만 `기본 시드 + 시도 번호`다(재시도 온도는 `retry_temperature`). `is_fallback`은 모델이 아니라 코드가 채운다. 보호·재시도는 조용히 지나가지 않고 `last_call_info`(시도 횟수, outcome, 보호 건수)와 응답 헤더 `X-Openit-Attempts`·`X-Openit-Protections`에 남긴다. 발화와 출력 원문은 로그·예외·헤더에 담지 않는다. 라이브러리 예외는 인프라·설정·입력 문제에만 쓰는 원칙은 그대로이며, 예외인 `ModelOutputInvalid`는 D75가 정한 것이다.

의미 검증(`targets`가 `values`의 부분집합, `next_item` 규칙, 건너뛰기 순서, `save` 규칙)은 이 영역이 하지 않는다. turn-engine의 `validate_extraction`을 survey-service가 부른다(D75). 이 영역은 스키마(형식)만 검증한다. 스키마의 `allOf/if/then`(action별 규칙)은 사후 검증이 한다(아래 §제약 디코딩).

## 보호 정책 `protect-2` (이 리포지토리의 해석, `protection.py`)
정책 버전은 `serving_config.protection_policy_version`에 기록되어 `serving_config_hash`에 반영된다. 바꾸려면 새 버전을 만든다. v1의 `protect-1`은 지원하지 않는다(적재 시 `UnsupportedServingConfig`).

| 이름 | 내용 |
| --- | --- |
| J1 | respond 형식: 앞뒤 공백을 뗀 문자열이 비어 있지 않으면 통과한다. 길이 상한은 두지 않는다(계약: 길이는 평가 지표, 미정). 출력이 `max_tokens`에서 잘렸는지는 백엔드가 알려 주지 않아 확인하지 못한다 |
| J2 | 대체 문장 조립(`fallback.py`, 순수 함수, 발화를 쓰지 않는다): `known`, `ask`, `hold`, `next.text` 순으로 비어 있지 않은 조각을 공백 하나로 잇고, 바로 앞과 같은 조각은 한 번만 쓴다. 재료가 하나도 없으면 `empty_fallback_text`("죄송합니다. 다시 한 번 말씀해 주세요.", **잠정**) |
| J3 | extract는 보호하지 않는다. 항목을 고치거나 비우지 않는다 |

보호 이름(`last_call_info.protections`, `X-Openit-Protections`): `G0`(respond 형식 위반, 원인), `G5`(대체 문장 사용). extract에는 보호 이름이 없다(실패는 예외).

### 제거한 것과 이유
- 파서 P1~P6: 값을 고치거나 비워 "다시 말씀해 주세요"로 잇는 규칙이었다. v2에서는 값을 지어내지 않는 쪽이 안전하다는 결정(D75)으로 없앴다.
- 생성기 G1(숫자 허용 범위), G4(위험 안내 문장 포함): v1의 응답 지시(`say_values`, `RISK_NOTICE`)에 묶여 있었다. v2 입력(`ResponseRequest`)에는 그에 대응하는 구조가 없다. **G1에 해당하는 검사(응답의 숫자가 입력 재료에 있는지)를 v2에서 다시 둘지는 미정**이다(제안: change-requests/004의 항목 3). 지금은 두지 않았다.
- G2, G3은 v1에서도 기계로 확인하지 않았다(프롬프트 지시). 지금은 프롬프트를 이 영역이 갖지 않으므로(시스템 프롬프트는 모델 패키지, D75) 해당 없음.

## 제약 디코딩과 `ActionExtraction`
vLLM(xgrammar)은 `allOf`·`if/then/else`·`not`을 조용히 무시하는 것이 v1 시험(design §11-R)에서 확인되었고, 이 때문에 완화(`relax-1`, `relax-2`)가 이를 뺀다. `relax-3`(0.6.5, D83)부터는 `allOf`의 `if/then(/else)`를 같은 뜻의 `anyOf` 변형으로 펼쳐 제약에 넣는다(펼치지 못하는 규칙만 제거하고 `CallInfo.relaxed`에 기록한다). 사후 검증은 그대로다. `ActionExtraction`은 action별 규칙을 `allOf/if/then`으로 쓰므로 **제약 디코딩만으로는 `reason`/`targets`/`note`의 action별 허용값과 `save`·`value`의 status별 규칙이 지켜지지 않는다.** 사후 검증(항상 거침)이 이를 잡고 재시도한다. `key`/`next_item`의 `pattern`과 `targets`의 `minItems`는 0.6.4(`relax-2`, D82)부터 엔진에 그대로 보낸다(xgrammar 0.2.7의 `pattern` 강제는 컨테이너 안 직접 시험으로 헤드가 확인, 이 리포는 재현하지 못했다). `relax-1`은 이를 빼서 extract가 모두 OUTPUT_INVALID가 났다. 재시도가 자주 일어나는지는 GPU 시험에서 `X-Openit-Attempts`로 본다.
