# model-runtime 경계

상태: 계약 2.0.0 기준(0.6.5). 정확한 시그니처는 `../boundary-contracts/INTERFACES.md`의 I9와 `HTTP.md`의 I9 절이 정한다.

## 소유 저장소
없음(공유 라이브러리 + 추론 서버 이미지).

## 제공 (I9)
라이브러리: `load`, `ModelSession.extract/respond`(LoRA-A/B), `extract_raw`/`respond_raw`(`RawResult`), `ResponseResult(text, is_fallback)`, `last_call_info`, `render_prompt`, `serving_config_hash`, `validate_serving_config`, `FakeModelSession`(+`FakeBackend`, `fake_serving_config(s)`), `RUNTIME_VERSION`, `SUPPORTED_RUNTIME_CONTRACT_VERSION`.
- `load(adapters, serving_configs, *, schema_dir, expected_serving_config_hashes, system_prompts, backend=None)`: **`system_prompts`가 INTERFACES I9의 시그니처에 없는 추가 인자**다(역할 → `{path, sha256}` = `ReleaseManifest.adapters[role].system_prompt`). 파일은 `adapters[role]` 디렉터리 안에서 읽고 해시가 다르면 `SystemPromptMismatch`. 계약 문서에 추가를 요청했다(change-requests/004).
- `render_prompt(role, input, *, serving_config, system_prompt, schema_dir)`: 시스템 프롬프트는 패키지 파일의 내용이다.
- 예외는 인프라·설정·입력 문제에만 쓴다: `ServingConfigMismatch`·`UnsupportedServingConfig`·`ContractVersionMismatch`·`ContractViolation`·`BackendNotAvailable`·`SystemPromptMismatch`(모두 `BoundaryError`가 아님). 예외인 **`ModelOutputInvalid`**는 extract가 재시도 후에도 `ActionExtraction`을 못 지킬 때 던진다(D75, 값을 지어내지 않는다). respond의 출력 문제는 예외가 아니라 대체 문장(`is_fallback`)이다.
- `schema_dir`(문진 스키마 배포 디렉터리 `.../schema/schemas`)는 `runtime_contract_version`(문진 스키마 정수 버전, D75) 확인에만 쓴다. 모델 입출력 스키마는 `boundary_contracts` 패키지에서 읽는다.

추론 서버 `python -m model_runtime.server`(HTTP.md I9): `PUT/GET /v1/releases/{id}`, `POST .../extract`(JSON), `POST .../respond`(`text/plain`), `GET /v1/info`, `/healthz`, `/readyz`. 응답 헤더 `X-Openit-Outcome`(extract는 `ok`, respond는 `ok|fallback`), `X-Openit-Attempts`, `X-Openit-Protections`(있을 때). extract가 끝내 실패하면 503 `UNAVAILABLE`, `details.reason=OUTPUT_INVALID`, `details.attempts`.
`/v1/info`는 계약 목록 외에 `system_prompt_sha256`(가장 최근 적재한 릴리스의 패키지 프롬프트 해시)을 낸다(추가 필드, 계약에는 없다).

## 소비
- **키 순서**: 제약 디코딩(xgrammar)은 스키마의 `properties` 순서대로 키를 낸다. 순서의 정본은 계약(v2.0.1부터 사전순)이며 `relax`는 순서를 바꾸지 않는다.
- `boundary-contracts` **태그 v2.0.1**: 스키마(`ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`, `BoundaryError`, 릴리스 읽기 `HttpReleaseReader`), `canonical`(해시). 지원 계약 버전: 2.x(1.x 레코드는 `UNSUPPORTED_VERSION`).
- 문진 스키마 정수 버전 1(`SUPPORTED_RUNTIME_CONTRACT_VERSION`, `CONTRACT_VERSION`). 다른 값이면 `ContractVersionMismatch`/422 `UNSUPPORTED_VERSION`.
- 릴리스 매니페스트의 `model_runtime_version`은 MAJOR.MINOR가 이 서버와 같아야 한다(0.6.x). 0.5.x로 만든 릴리스는 거부된다.

## 백엔드와 프로파일
- 첫 실제 백엔드는 vLLM이다(선택 의존 `.[vllm]`). `serving_config.constraint.backend`는 `"vllm"`, `backend_version`은 설치된 vLLM 버전, `supported_features`는 `DEFAULT_SUPPORTED_FEATURES`여야 `load`가 받는다.
- **기반 모델은 프로파일로 고른다(D74).** `gpu-12g`는 Qwen3-1.7B(파이프라인 데모용, D81), `rtx4090-24g`는 Qwen3-8B. 학습된 4B/8B 어댑터는 24GB급 이상에서 서빙한다. 두 프로파일의 **적재 가능 여부와 A·B 어댑터 동시 적재는 미확인**(측정 전, 파일의 `status`). 어댑터 디렉터리의 `adapter_config.json` 기반 모델이 프로파일의 기반 모델과 다르면(또는 `r > max_lora_rank`) 적재를 거부한다(412 `FAILED_PRECONDITION`).
- LoRA-B는 JSON이 아니라 텍스트라 제약 디코딩에 스키마를 넘기지 않는다(`constraint_mode`가 `constrained`여도 B에는 적용하지 않는다).

## 호출자가 지켜야 할 것
- 역할마다 `ModelPackageManifest.serving_config`와 `serving_config_hash`를 `serving_configs[role]`, `expected_serving_config_hashes[role]`로, `system_prompt`를 `system_prompts[role]`로 넘긴다. 역할 집합은 {LORA_A, LORA_B}로 같아야 한다.
- 역할별 설정이 달라도 `constraint`(백엔드 이름·버전·지원 기능)는 두 역할이 같아야 한다.
- `prompt_template_version`은 `prompt-4`(권장·기본, D77), `protection_policy_version`은 `protect-2`(v1의 `prompt-2`/`protect-1`은 거부). `prompt-3`(JCS 직렬화)은 호환용으로만 받으며 **폐기 예정**이다. 서버에서 학습한 어댑터(dataset_10_14)에는 `prompt-4`를 쓴다.
- **입력 객체의 키 순서는 호출자의 책임이다(prompt-4).** 서비스는 `json.dumps(obj, ensure_ascii=False)`(기본 구분자)로 직렬화하며 학습 글자와 한 글자도 달라서는 안 된다. 호출자가 준 순서를 보존하되 다음만 이 서버가 정렬한다: LORA_A는 **최상위 키**(current_question, question_id, state, user_utterance 순), LORA_B는 **모든 수준**의 키. 따라서 survey-service는 LORA_A의 `state` 안(키와 그 아래)을 **학습 때의 삽입 순서**로 만들어 넘겨야 한다(서버가 바꾸지 않는다). 근거: 최상위 순서는 의미가 없고 호출자마다 달라지기 쉬워 서버가 강제하고, `state` 순서는 스키마로 복원할 수 없어 호출자만 안다. B는 학습 데이터가 전 수준 정렬이고 객체 키 순서에 의미가 없다.
- **extract의 503 OUTPUT_INVALID는 호출자가 한 번 다시 보내고, 그래도 안 되면 턴을 실패 신호로 처리한다(INTERFACES I9 '서비스가 지키는 것').**
- 학습 데이터 변환은 `render_prompt(..., system_prompt=<패키지의 프롬프트>, serving_config=...)`를 쓴다.

## 장애 영향
- extract: 모델 출력이 깨지면 재시도 후 `ModelOutputInvalid`(503 OUTPUT_INVALID). 서비스가 턴을 실패 처리하고 세션 상태를 바꾸지 않는다. 보호가 아니라 실패다.
- respond: 모델 출력이 깨지면 재료를 이은 대체 문장(`is_fallback`)이 나간다. 턴은 실패하지 않는다.
- 로딩 거부(해시·프롬프트 불일치, 버전, 기반 모델)는 릴리스 적재 실패로 이어진다. 이전 릴리스를 유지하는 책임은 서비스에 있다(I4).
- 개인정보: 로그·예외·응답 헤더에 발화·모델 출력 원문을 담지 않는다.
