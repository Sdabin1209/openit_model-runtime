> **닫힘(2026-10-15, D62).** 헤드가 `boundary-contracts/HTTP.md` "I9 ModelRuntime 추론 서버"로 확정했고 서버를 그에 맞췄다(릴리스 단위 경로, `X-Openit-*` 헤더). 아래 본문은 당시 제안이며 기준은 HTTP.md다.

# 변경 요청 003: model-runtime 추론 서버의 HTTP 규약 (헤드에게)

작성: model-runtime 에이전트, 2026-10-15 (D62). `survey-service`의 `inference_server`는 읽을 수 없어(경계) 그 규약을 **확인하지 못했다**. 아래는 model-runtime이 구현한 규약이다. `HTTP.md`에는 I9 항목이 없다. survey-service의 `SURVEY_INFERENCE_URL` 호출과 다르면 어느 쪽을 맞출지 정해 달라(model-runtime 쪽은 경로와 응답 헤더만 바꾸면 된다).

| 호출 | 요청 | 응답 |
| --- | --- | --- |
| parse | `POST /v1/parse` 본문 `UtteranceParserInput` | 200 `ParserOutput` |
| generate | `POST /v1/generate` 본문 `ResponseGeneratorInput` | 200 `GeneratorOutput`(`is_fallback` 포함) |
| 상태 | `GET /healthz`(살아 있음), `GET /readyz`(세션 적재됨, 아니면 503) | 200 `{"status": ...}` |
| 정보 | `GET /v1/info` | 버전, 백엔드, 역할별 `serving_config_hash` |

- 오류는 CONVENTIONS 9절의 `BoundaryError` 본문: 입력 계약 위반 400 `INVALID_RECORD`, 토큰 불일치 403, 엔진 실패·미준비 503 `UNAVAILABLE`(재시도 가능). 모델 출력이 깨진 것은 오류가 아니라 보호가 낸 대체 출력의 200이다(D36).
- 보호 여부는 본문을 바꾸지 않고 응답 헤더로 낸다: `X-Model-Runtime-Outcome`(ok|repaired|fallback), `X-Model-Runtime-Attempts`, `X-Model-Runtime-Protections`(`P1:1,G5:1` 형식). 호출자가 집계해 `TurnTrace` 등에 남길 수 있다.
- 인증: `MODEL_RUNTIME_TOKENS`가 설정되면 `Authorization: Bearer`가 필요하다(역할 구분 없음, 단일 호출자 가정).
- 요청은 한 번에 하나씩 처리한다(엔진 직렬화). 지연 목표가 정해지면 동시성(`max_num_seqs`)을 다시 본다.
- 서버는 서빙 설정을 `MODEL_RUNTIME_SERVING_CONFIGS`(매니페스트의 `serving_config`와 해시)로 받는다. 릴리스 적재(I4 `load_bundle`)와 이어 붙이는 일, 릴리스 교체 시 서버 재기동 여부는 survey-service/deploy와 정할 일이다(미정).
