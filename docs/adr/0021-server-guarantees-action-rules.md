# ADR 0021: LoRA-A 출력 규칙을 model-runtime이 보증한다

## 상태
채택(2026-10-29, 헤드 지시 단위 2). 근거 결정: 계약 ADR 0010(결정 5), D104, D108. CR-007(wo6 메모)을 구현한다.

## 맥락
LoRA-A(`extract`) 출력은 스키마(`ActionExtraction`)를 통과해도 규칙을 어길 수 있다(저장 규칙, next_item과 문항 순서·건너뛰기의 어긋남 등). 지금까지 이 검사는 turn-engine의 `validate_extraction`이 호출자(survey-service) 쪽에서 했다. 구조 규칙 R7은 한 컴포넌트가 보증한 값을 다른 컴포넌트가 다시 검사하지 않게 한다. turn-engine은 퇴역 예정이다.

## 결정
1. extract 요청 본문으로 계약 `ExtractRequest{input, flow}`를 받는다. 2.x 동안은 옛 본문(`ActionExtractionInput`)도 받는다. 형식은 스키마(`ExtractRequest`, 아니면 `ActionExtractionInput`)로 판정하고 둘 다 아니면 400.
2. `flow`가 있으면 `ExtractRole`이 스키마 위반이 없는 출력에 `check_action_extraction(input, output, flow)`를 적용하고, 위반을 출력 무효로 본다. 기존 재시도(`max_attempts`, 호출 예산)와 503 `OUTPUT_INVALID`를 그대로 쓴다. 위반은 `rule:<코드>:<경로>`로 세고 값은 싣지 않는다.
3. 입력 길이 한도(ADR 0012의 2500자)는 `input`에만 건다. `flow`는 프롬프트에 들어가지 않아 컨텍스트를 쓰지 않는다. 프롬프트는 `input`만으로 만든다(학습 글자 불변, D77).
4. 라이브러리 `ModelSession.extract(input, flow=None)`도 같은 규칙이다.
5. 시험의 turn-engine 의존을 없앤다. 기대값은 계약의 검사기, 흐름표는 문진 스키마의 `data/question_flow.json`을 `QuestionFlowTable`(order, skips)로 만든 것이다. 사본을 두지 않는다.

## 왜
- 따른 표준: 프로젝트 구조 규칙 R7과 계약 ADR 0010, HTTP.md I9 "extract 출력 규칙 보증". 규칙 본문은 계약의 데이터(`action_extraction_rules.json`)라 이 영역에 복사하지 않고 기준 검사기를 호출한다.
- 모델을 아는 곳은 이 영역이고, 재시도로 만회할 수 있는 곳도 여기뿐이다. 호출자가 검사하면 위반을 알아도 같은 요청을 한 번 더 보내는 것 말고는 할 수 없다.
- 형식 판정을 스키마로 하는 이유: `flow` 키 유무로 가르면 `flow`가 빠진 `{input}` 같은 반쯤 맞는 본문을 옛 형식으로 조용히 읽는다.
- 위반 검사를 스키마 통과 뒤에만 하는 이유: 검사기는 출력이 구조를 갖췄다고 가정하는 규칙을 본다. 스키마 위반 값에는 스키마 위반 이름이 더 정확하다.

## 검토한 대안과 버리는 이유
- 호출자(survey-service)가 재검증: R7 위반이고 규칙이 두 곳에 있게 된다. 기각.
- 규칙 코드를 이 영역에 복사: 계약 데이터와 어긋난다. 기각.
- 본문 판정을 `flow` 키 유무로: 위 이유로 기각.
- `input`+`flow` 합산에 한도를 걺: `flow`는 모델에 안 들어가므로 정상 요청을 거절한다. 기각.

## 결과
얻는 것: 호출자는 통과한 값을 믿고 쓴다. 의미 위반이 재시도로 만회된다. 감수하는 것: 규칙 위반이 잦으면 재시도로 호출 예산(ADR 0020)을 쓰고 `OUTPUT_INVALID`가 늘 수 있다. 검사기 호출 비용과 위반 빈도는 측정하지 않았다(가짜 백엔드 시험만). 옛 본문은 3.0.0 전까지 규칙 보증이 없다. 실제 모델에서 규칙 위반 비율은 미확인이다.

## 되돌리는 조건
계약 3.0.0에서 `ExtractRequest`만 남으면 옛 본문 분기를 지운다. 검사기 규칙 버전이 바뀌면 계약 태그를 따른다.
