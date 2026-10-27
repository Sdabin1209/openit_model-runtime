# 변경 요청 005: 모델 입력 문자열에 maxLength

- 요청자: model-runtime (감사 MR-3). 대상: boundary-contracts(헤드 소유). 상태: 제안.
- 부족한 것: `ActionExtractionInput.user_utterance`, `current_question`, `ResponseRequest`의 문자열 필드에 `maxLength`가 없다. 서버는 JSON 직렬화한 입력 전체를 2500자로 제한하지만(docs/adr/0012) 호출자가 사전에 검증할 수 있는 계약 상한이 없다.
- 제안: 필드별 `maxLength`(예: `user_utterance`)를 스키마에 추가하고 값은 `max_model_len`(gpu-12g 4096 토큰)과 시스템 프롬프트 길이에서 도출한다. 구체 값은 토큰 길이 측정 뒤 헤드가 정한다(이 영역은 측정하지 않았다).
- 영향: 호출자(survey-service)는 상한 초과 발화를 서버에 보내기 전에 처리한다. 추가는 MINOR 변경 절차를 따른다.
