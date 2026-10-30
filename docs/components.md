# 컴포넌트 (D117, U400)

구조 규칙 6개(D104)에 따른 이 영역의 컴포넌트 표다. 정본 배치는 `head/docs/DESIGN.md`. 위에서부터 나눈 근거: 이 영역의 입력은 계약(boundary-contracts v3.1.1)과 `docs/brief.md`이고, 그 일(프롬프트 조립 -> 모델 호출 -> 출력 보장 -> 서버 면)을 업무 단계로 나눈 것이다. 기존 코드는 정의의 근거가 아니라 빠진 거부 조건을 찾는 대조 자료로만 썼다.

컴포넌트는 `components/<이름>.py`에 파일 하나이고, 모듈 맨 위에 `INPUT, OUTPUT = "<입력 스키마>", "<출력 스키마>"`를 선언한다(`schemas/<이름>.json`). 입력·출력 검사는 wiring의 `schema_gate.gate`가 컴포넌트 앞뒤에서 스키마로 한다. 컴포넌트끼리는 import하지 않는다(`tests/test_structure.py`). 컴포넌트가 필요로 하는 다른 컴포넌트의 일(재시도, 출력 검증, 대체 문장 등)은 wiring이 함수로 주입한다.

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
| --- | --- | --- | --- | --- |
| prompt_render | `components/prompt_render.py` | PromptRenderInput | PromptText | 시스템 프롬프트와 입력 JSON을 채팅 형식 프롬프트로 조립한다(prompt-4, 특수 토큰 무력화). 순수 함수 |
| io_validate | `components/io_validate.py` | IoValidationInput | IoValidationResult | 역할 하나의 입력·출력을 계약 스키마(ActionExtractionInput/ActionExtraction, ResponseRequest/ResponseText)로 검증해 위반 목록을 낸다 |
| constraint_relax | `components/constraint_relax.py` | RelaxInput | RelaxResult | 계약 출력 스키마를 제약 디코딩 엔진이 받을 수 있게 같은 뜻으로 완화한다(relax-3). 순수 함수 |
| survey_version_check | `components/survey_version_check.py` | SurveyVersionCheckInput | SurveyVersionCheckResult | 문진 스키마 정수 버전이 지원하는 값(1)인지 본다 |
| serving_config_check | `components/serving_config_check.py` | ServingConfigCheckInput | ServingConfigCheckResult | 서빙 설정의 지원 여부와 백엔드 일치를 보고 해시(JCS)를 낸다 |
| system_prompt_read | `components/system_prompt_read.py` | SystemPromptReadInput | SystemPromptText | 어댑터 디렉터리의 시스템 프롬프트 파일을 해시와 대조해 읽는다 |
| fallback_text | `components/fallback_text.py` | FallbackTextInput | FallbackText | 응답 요청의 재료를 이어 붙여 대체 문장을 만든다(출력 보호 정책 protect-2) |
| retry_loop | `components/retry_loop.py` | AttemptSpec | AttemptsResult | 시도 횟수·시드·재시도 온도·마감 규칙으로 생성을 반복한다(백엔드·파서·검사는 인자) |
| extract_role | `components/extract_role.py` | ExtractCall | ExtractOutcome | LoRA-A 업무: 파싱, 스키마·흐름 규칙·값 규칙 검사, 값 규칙 적용. 끝내 실패하면 값을 지어내지 않는다 |
| respond_role | `components/respond_role.py` | RespondCall | RespondOutcome | LoRA-B 업무: 문장 형식 검사, 실패하면 대체 문장 |
| fake_backend | `components/fake_backend.py` | GenerationRequest | GeneratedText | GPU 없이 호출 경로를 시험하는 가짜 모델 |
| mimic_backend | `components/mimic_backend.py` | GenerationRequest | GeneratedText | 데모용 모방 모델(규칙표 기반, 결정적) |
| vllm_backend | `components/vllm_backend.py` | GenerationRequest | GeneratedText | 실제 모델(vLLM + LoRA + 제약 디코딩). 엔진을 올리는 자원 설정은 생성자 |
| flow_file_load | `components/flow_file_load.py` | FlowFileRef | FakeFlowSpec | 문항 흐름 파일을 읽고 계약 RuntimeFlowFile로 검증해 가짜·모방 백엔드가 쓸 형태로 낸다 |
| token_table | `components/token_table.py` | TokenTableInput | TokenTable | `역할=토큰,...` 글자를 표로 바꾼다(중복·빈 값 거부) |
| runtime_dirs_check | `components/runtime_dirs_check.py` | RuntimeDirsCheckInput | RuntimeDirsCheckResult | 시작 때 번들·모델 캐시·런타임 캐시 디렉터리를 쓸 수 있는지 본다 |
| release_registry | `components/release_registry.py` | InferRequest / PutRequest | HttpResult | 릴리스 적재 상태, 추론 호출의 직렬화·대기·마감을 맡는 함수 모양 면(put, get, info, infer, readiness). HTTP를 모른다 |
| link_receive | `components/link_receive.py` | (HTTP 요청, 계약 스키마) | HttpResult | 받는 쪽 통신: 추론 서버의 경로·역할을 통신 기반 라이브러리 `openit_link.App/Route` 위에 적는다 |
| link_release_fetch | `components/link_release_fetch.py` | PutRequest | HttpResult | 보내는 쪽 통신: release-gate(I4)에서 번들을 읽고(`openit_link.clients.HttpReleaseReader`) 서버 도달성을 본다 |

## wiring

| 파일 | 하는 일 |
| --- | --- |
| `wiring/session.py` | `load`, `FakeModelSession`: 컴포넌트를 연결한다(프롬프트 -> 재시도 -> 역할 -> 예외로 바꾸기). 공개 API |
| `wiring/schema_gate.py` | 스키마로 컴포넌트의 입력·출력을 검증하는 문지기 |
| `wiring/schema_set.py`, `wiring/prompt_api.py` | 공개 `SchemaSet`, `render_prompt` |
| `wiring/service.py` | release_registry에 세션 적재·도달성 확인·시작 점검·버전을 주입 |
| `wiring/backends.py` | 모방 백엔드를 흐름 파일로 조립 |
| `wiring/ports.py`, `wiring/errors.py`, `wiring/results.py` | 백엔드 포트, 영역 공용 예외 어휘, 호출 결과 타입의 공개 이름 |

## 진입점·공개 이름 (컴포넌트가 아니다)

- `server.py`: 환경 변수 설정 읽기와 기동(`main`, `build_service`). 업무 규칙 없음.
- `__init__.py`, `prompts/__init__.py`, `schema_guard/__init__.py`: 공개 API(다른 영역이 import하는 이름)를 새 위치에서 다시 내보낸다. 이름은 바뀌지 않았다.

## 바뀐 것과 바뀌지 않은 것

- 바뀌지 않음: 공개 이름(`load`, `render_prompt`, `FakeModelSession`, `serving_config_hash` 등), HTTP 경로·상태 코드·본문·헤더, 잡 출력, 이미지 라벨 키.
- 통신 기반 라이브러리(`openit_link`, D118)로 옮기며 달라진 것: 인증이 없는 요청이 존재하지 않는 경로를 부르면 401이 아니라 404다(라이브러리는 경로를 먼저 찾는다). `/readyz`의 사유 본문은 인증 여부와 상관없이 내지 않는다(라이브러리의 `/readyz`는 인증하지 않는다). 요청 본문 위반의 메시지 문구가 라이브러리 것이다(상태 코드 400, 코드 INVALID_RECORD는 같다).
