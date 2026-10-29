# ADR 0024: extract 서버가 요청의 `fields`로 값 규칙을 적용한다

## 상태
채택(2026-10-30, 헤드 지시 D115, 계약 v2.4.0 HTTP.md I9 2.4.0 문단). [ADR 0021](0021-server-guarantees-action-rules.md)을 확장한다.

## 맥락
`check_action_extraction`을 통과한 출력도 칸 규격(자료형·최소·최대)과 값 규칙(R1~R3: 횟수가 기간보다 많음, 안 마신다면서 양이 있음, 최대량이 평소량보다 적음)을 어길 수 있다. 데이터셋 정규화기는 이런 상황에 정해진 모양의 정답을 낸다. 계약 2.4.0이 `ExtractRequest.fields`(QuestionFieldSpec)와 기준 구현 `apply_value_rules(input, output, fields, flow)`를 더했다.

## 결정
1. `fields`가 있으면 `check_action_extraction` 통과 뒤에 `apply_value_rules`를 적용한다. 없으면 이전 동작이다(2.x 호환).
2. 적용 사유가 있으면(범위 밖, R1~R3) **다시 생성하지 않고** 바뀐 출력을 200으로 낸다(데이터셋 정답 모양). 바뀐 출력은 `ActionExtraction` 스키마와 `check_action_extraction`을 다시 통과해야 한다. 통과하지 못하면(기준 구현끼리 어긋남) 내보내지 않고 위반 `rule:AFTER_VALUE_RULES_<코드>`로 다시 생성한다.
3. 바꿀 수 없는 위반(`VALUE_TYPE_MISMATCH`, `VALUE_BELOW_MINIMUM`)은 기존 형식 위반과 같은 경로로 다시 생성하고, 끝내 남으면 503 `OUTPUT_INVALID`(`details.violations`에 `rule:<코드>:<경로>`).
4. 적용 사유는 로그(`값 규칙 적용 reasons=…`)와 응답 헤더 `X-Openit-Protections`(`OUT_OF_RANGE:1` 같은 사유별 건수)에 이름만 남기고 값은 남기지 않는다. 헤더 형식은 계약 클라이언트의 정규식(영문자로 시작, 영숫자·밑줄)에 맞는다.
5. `ModelSession.extract(input, flow=None, fields=None)`도 같다. 입력 길이 한도는 `input`에만 건다(ADR 0021).

## 왜
- 따른 표준: 계약 HTTP.md I9 2.4.0, 구조 규칙 R7(통과한 출력을 호출자가 다시 검사하지 않음), D74(서비스를 데이터셋에 맞춘다). 규칙 본문은 계약 데이터라 이 영역에 복사하지 않는다.
- 다시 생성하지 않는 이유: 규칙에 걸린 출력의 정답이 데이터셋에 정해져 있어 모델에 다시 묻는 것은 호출 예산(ADR 0020)만 쓴다. 정답이 없는 위반만 다시 생성한다.

## 검토한 대안과 버리는 이유
- 값 규칙을 호출자가 적용: R7 위반, 규칙이 두 곳. 기각.
- 바꾼 출력을 검사 없이 내보내기: 기준 구현이 서로 어긋나면 호출자가 깨진 출력을 받는다. 기각.

## 결과
얻는 것: 호출자는 값 규칙까지 보증된 출력을 받는다. 감수하는 것: 검사기 비용과 규칙 적용 빈도는 측정하지 않았다(가짜 백엔드 시험만). 실제 모델이 범위 밖 값을 내는 비율은 미확인이다. 모방(mimic) 백엔드는 규칙을 지키는 출력만 내도록 만들어져 범위 밖 장면은 가짜 백엔드(slots)로 만들었다.

## 되돌리는 조건
계약 3.0.0에서 `fields`가 필수가 되면 없는 경우의 분기를 지운다.
