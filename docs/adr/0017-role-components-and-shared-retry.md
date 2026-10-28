# ADR 0017: LoRA-A·LoRA-B를 두 컴포넌트로 나누고 재시도는 공용 함수 하나

## 상태
채택(wo2 브랜치, 감사 MR-01·MR-02·MR-03의 최소 수정).

## 맥락
`_Session`의 한 반복문이 `role` 분기로 A(JSON, 제약 디코딩, 실패 시 503)와 B(문장, 제약 없음, 실패 시 대체 문장) 일을 같이 했고, 프롬프트 직렬화·스키마 완화·가짜 응답기도 역할 종류로 분기했다. 한쪽을 바꾸면 다른 쪽 분기를 건드린다. 특수 토큰 처리는 서버에 있어 직접 호출자와 서비스의 프롬프트가 달랐다.

## 결정
- `roles.py`에 `ExtractRole`(입력 `ActionExtractionInput`, 출력 `ActionExtraction`)과 `RespondRole`(입력 `ResponseRequest`, 출력 `ResponseText`)를 둔다. 각자 프롬프트·파싱·검증·실패 처리를 갖고 서로를 import하지 않는다. 세션은 두 객체에 위임만 한다.
- 시도 횟수·시드 증가·재시도 온도는 두 역할이 같은 계약이라 `run_attempts` 한 함수에 둔다.
- 프롬프트 직렬화는 함수 둘(`serialize_extract_input_4`, `serialize_respond_input_4`)이고 버전 표가 역할 이름으로 가리킨다. 특수 토큰 처리(`neutralize_special_tokens`)도 `prompts.py`의 `render_prompt` 안에 둔다(검증 뒤, 직렬화 전).
- `SchemaSet`은 역할 종류를 가리지 않는다(제약 스키마를 쓸지는 역할이 정한다). 가짜 백엔드의 응답기는 어댑터 이름으로 역할별 응답기를 고른다(`route_by_adapter`).
- 공개 API(I9 서버, `ModelSession`, `load`, `render_prompt`)와 한 엔진 두 어댑터 구조(ADR 0003)는 바꾸지 않는다. 역할 이름은 백엔드가 어댑터를 고르는 조회 키(`GenerationRequest.role`)로만 남는다.

## 왜 (따른 표준과 근거)
워크스페이스 구조 규칙 R3·R4(컴포넌트는 자기 입출력 스키마와 업무만 안다). 재시도를 한 곳에 두는 이유는 INTERFACES I9가 두 역할에 같은 재시도 계약을 주기 때문이다. 특수 토큰을 프롬프트 조립으로 옮긴 이유는 ADR 0011이 "같은 입력은 같은 프롬프트"를 수용 기준으로 삼았기 때문이다.

## 검토한 대안과 버리는 이유
- 역할별 세션 클래스 둘: 공개 `ModelSession` 모양(한 객체에 extract/respond)이 바뀌어 호출자가 영향을 받는다. 버린다.
- 재시도를 역할마다 복사: 시드·온도 규칙이 어긋날 위험. 버린다.
- 서버 파일 분리(MR-03의 나머지): 이번 최소안에 넣지 않았다(미착수).

## 결과
얻는 것: 한 역할의 변경이 다른 역할 코드에 닿지 않는다. 직접 호출자도 서버와 같은 프롬프트를 만든다. 감수하는 것: 서버의 `neutralize_special_tokens` 모듈 속성이 없어졌다(`model_runtime.prompts`로 이동). `GenerationRequest.input`(MR-04)과 서버의 `info()`가 내부 표를 읽는 문제(MR-03)는 그대로다.

## 되돌리는 조건
세 번째 역할이 생기면 같은 모양의 객체를 `ROLE_TYPES`에 더한다. 두 역할의 재시도 계약이 갈라지면 `run_attempts`를 역할별로 나눈다.
