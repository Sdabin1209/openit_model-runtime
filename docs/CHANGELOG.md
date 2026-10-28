# 변경 이력

## 0.8.1 — 2026-10-29 (계약 v2.2.0 핀, 의존 핀만)
PATCH: 의존 핀만 바꾸고 코드 동작은 바꾸지 않는다(헤드 지시 wo-bc220). ADR 0010의 `check_action_extraction` 연결(CR-007)은 다음 단위다.
- 계약 핀 boundary-contracts v2.1.1 → **v2.2.0**(정본: `Dockerfile`의 `ARG BOUNDARY_VERSION`, `setup-env.sh`·`smoke.sh`·`gpu_check.sh`가 여기서 읽는다). 이 영역 서버의 401/403과 호출별 시간 상한(0.8.0)은 2.2.0 문서가 확정한 규칙이다.
- turn-engine 시험 의존의 기본 태그 → **v0.4.3**(`scripts/setup-env.sh --with-turn-engine`, `tests/helpers.py`의 `TURN_ENGINE_TAG_DEFAULT`). v0.4.3 태그(커밋 `a60f77e`)를 venv에 설치해 전체 시험을 돌렸다.
- 이미지 라벨을 계약 v2.2.0 `ImageLabels`(D108)에 맞춘다: `Dockerfile`의 `SURVEY_SCHEMA_VERSION`·`TURN_ENGINE_VERSION` 기본값 `unknown`·`none` → `n/a`, `scripts/smoke.sh`가 넘기는 `io.openit.model-runtime` 값에 `v` 접두사(`v0.8.1`). 시험 `tests/test_image_labels.py`가 Dockerfile ARG·LABEL과 smoke.sh 빌드 인자로 라벨을 만들어 계약 검증기로 본다(수정 전 2건 실패를 확인). 서버 동작은 그대로다. **배포 쪽 호환 주의:** 이 이미지를 직접 빌드하는 쪽은 `MODEL_RUNTIME_VERSION`에 `v`를 붙여 넘겨야 한다.

## 0.8.0 — 2026-10-29 (계약 2.2.0 단계 B: 인증 실패 401, 호출별 시간 상한)
0.x 규칙에 따라 호환이 깨지는 변경이라 MINOR를 올린다(헤드 지시 wo7). 계약 pin(boundary-contracts v2.1.1)은 이 변경에서 그대로다. 코드는 wo5(커밋 `82ddb56`, `43b6884`)에서 바뀌었고 이 버전 올림은 버전 표기와 문서만이다.
- **호환 깨짐 1: 환경 변수 `MODEL_RUNTIME_REQUEST_DEADLINE_S`(3.5초 하나) 제거.** 설정되어 있으면 서버가 시작을 거부한다. 대신 `MODEL_RUNTIME_EXTRACT_DEADLINE_S`(기본 3.0), `MODEL_RUNTIME_RESPOND_DEADLINE_S`(기본 2.0)를 쓴다. 출력 재시도는 남은 호출 예산이 직전 시도 시간 이상일 때만 한다(ADR 0020, 계약 CONVENTIONS '시간 상한 사슬' 2.2.0). 이 배분값은 측정하지 않았다.
- **호환 깨짐 2: 토큰이 없거나 틀리면 403이 아니라 401 + `WWW-Authenticate: Bearer`.** 권한 없음(`PUT_ROLES` 밖, `loader`의 추론)만 403. 오류 본문 `code`는 둘 다 `PERMISSION_DENIED`, 클라이언트 변경 없음(계약 CONVENTIONS 6절, D106; ADR 0012 갱신).
- **호환 깨짐 3: 릴리스 매니페스트의 `model_runtime_version`은 MAJOR.MINOR가 서버와 같아야 하므로(`server.py`), 0.7.x로 만든 릴리스는 0.8.0 서버에서 `UNSUPPORTED_VERSION`으로 거부된다.** 릴리스를 0.8.x로 다시 만들어야 한다.
- 문서: ADR 0019(근거 없는 값의 출처 분류), ADR 0020(호출별 상한), design.md의 `gpu-12g` 설명 정정(Qwen3-1.7B).

## 0.7.7 — 2026-10-28 (역할 컴포넌트 분리, 특수 토큰 한 곳, 모방 백엔드)
공개 API(I9 서버, `ModelSession`, `load`, `render_prompt`)와 `serving_config`·해시는 그대로다. 버전은 0.7.7로 올렸다(헤드가 병합 때 정했다). 계약 v2.1.1 pin은 그대로다.
- MR-02: `neutralize_special_tokens`를 서버에서 `prompts.py`로 옮기고 `render_prompt`가 적용한다(검증 뒤, 직렬화 전). 직접 호출자도 서비스와 같은 프롬프트를 만든다. `server.neutralize_special_tokens`는 없어졌다.
- MR-01·MR-03(최소안): `roles.py`의 `ExtractRole`/`RespondRole`, 공용 `run_attempts`, 프롬프트 직렬화 함수 둘, 가짜 응답기 역할별 분리(ADR 0017). 서버 파일 분리와 `info()` 정리는 하지 않았다.
- 모방 백엔드: `MODEL_RUNTIME_BACKEND=mimic`(`MODEL_RUNTIME_ALLOW_FAKE=1` 필요), `MimicExtractor`/`MimicResponder`, 규칙표 `data/mimic_rules.json`(ADR 0018). `/v1/info`의 backend는 `mimic`이고 매니페스트 `serving_config.constraint.backend`도 `mimic`이어야 한다.
- `scripts/setup-env.sh --with-turn-engine`: 모방 시험이 쓰는 turn-engine을 태그로 설치한다.

## 0.7.6 — 2026-10-28 (계약 v2.1.1 정렬)

계약 v2.1.1 정렬. 0.7.5는 계약 v2.1.0 pin이라 새 열차와 맞지 않아 0.7.6으로 대체한다(0.7.5 태그를 이미지·배포 기준으로 쓰지 않는다). 계약 pin을 `pyproject.toml` 주석, `Dockerfile`, `scripts/smoke.sh`, `scripts/gpu_check.sh`에서 v2.1.1로 올렸다. 버전 정본은 `pyproject.toml`의 version이고 `RUNTIME_VERSION`과 같다(`tests/test_version_source.py`). 같은 열차에서 문서의 복사된 버전을 정본 가리킴으로 바꾸고 `docs/design.md`의 v1 서술을 `docs/history/design-v1.md`로 옮겼다. 스모크 라벨 검사는 pyproject와 설치된 `RUNTIME_VERSION` 두 출처와 비교하고, Dockerfile은 `MODEL_RUNTIME_VERSION` 빌드 인자가 비면 실패한다. 동작 변경은 없다.

## 0.7.5 — 2026-10-28 (가짜 A 되묻기, GPU 세션 키트, 계약 v2.1.0 정렬)
**0.7.4는 계약 v2.0.2 pin이라 정렬되지 않아 폐기하고 0.7.5로 대체한다.** 0.7.4 태그가 원격에 올라갔으나 이미지·배포의 기준으로 쓰지 않는다. 0.7.5는 0.7.4의 내용에 더해 boundary-contracts 의존을 `v2.0.2`에서 `v2.1.0`으로 올렸다(`Dockerfile`의 `BOUNDARY_VERSION`, `scripts/gpu_check.sh`, 설치 예시 문서). 이 리포는 turn-engine 버전을 핀하지 않는다(U15). 열차 순서: boundary v2.1.0 -> turn-engine v0.4.1 -> model-runtime v0.7.5 -> 영역(D80).
아래는 0.7.4로 쓴 내용 그대로다(버전 표기만 0.7.5).
v0.7.3 이후 쌓인 변경의 정식 릴리스. HTTP 경로·계약 스키마·`serving_config`와 해시는 그대로이고, 운영(vLLM) 경로의 서버 동작은 바뀌지 않았다. 바뀐 서버 쪽 코드는 가짜 백엔드의 `reasks`뿐이다. 버전 표기(`pyproject.toml`, `RUNTIME_VERSION`, 이미지 라벨 `io.openit.model-runtime`, `scripts/smoke.sh`)를 0.7.4로 올렸다.
- 가짜 A `reasks`(아래 첫 항목): deploy E2E 5n-reask와 survey-service 되묻기 한도(REASK_LIMIT) 통합 시험용.
- GPU 세션 키트(`scripts/gpu_session`, ADR 0013~0016): `pod_setup.sh`(파드 이미지 차이를 스스로 준비: uv 고정 + 독립 파이썬, gcc는 root면 apt, 단계 분리, 모델 이름은 프로파일 base_model 한 곳), `pod_run.sh`, `util_retry.py`(GPU 사용률 자동 재시도), `run_session.sh --plan`(다단계 부하), 키트 커밋 기록, `--base` 한 곳에 설치·캐시 모으기, 실제 어댑터·C1 사전 점검. 모두 가짜 백엔드와 로컬 시험으로만 확인했고 **실제 GPU 한 바퀴를 새로 돌려 확인한 것은 아니다**.
- 실제 데이터 대조 도구 `scripts/check_reference_data.py`(OPEN_GAPS U03).
아래 세 절은 0.7.5에 들어간 변경의 상세다(당시 제목은 "0.7.3 이후").

### 2026-10-28 가짜 A의 되묻기(REASK) 설정
가짜 백엔드 전용, 운영(vLLM) 경로 무변경. 흐름 파일(`MODEL_RUNTIME_FAKE_FLOW_FILE`)의 선택 항목 `reasks`: `[{"contains","key","reason"(선택, 기본 MISSING),"at"(선택)}]`. 문구가 발화에 있으면 매번 REASK(next_item=입력 question_id, targets=[key], 그 칸은 MISSING)를 낸다. survey-service의 되묻기 한도(REASK_LIMIT)를 통합으로 시험하려고 더했다. `load_fake_reasks`를 내보낸다. 시험 `FakeReask`(tests/test_fake_flow.py).

### 2026-10-28 GPU 세션 키트 안전 보강(감사 라운드 2 MR-1~6)
서버 런타임 코드 변경 없음(scripts/gpu_session만): 작업 폴더 검사를 만들기 전에, `--clean-cache`·`--overwrite`는 표식이 있는 것만, `--release-id` 검증, 값 없는 옵션은 종료 코드 2, 토큰은 `curl -H @파일`. 호출자가 먼저 끊어도 엔진을 최대 3.5초 더 잡는 한계를 boundary.md에 적었다(추정). 버전은 올리지 않았다.

### 2026-10-28 실제 데이터 대조(OPEN_GAPS U03)
코드 변경 없음. `scripts/check_reference_data.py`와 선택 시험 `tests/test_reference_data.py`(`MODEL_RUNTIME_REFERENCE_DATA_DIR`가 없으면 건너뜀, 데이터는 리포에 없다)를 더했다. 최종 학습 데이터 C1 전체(A 32,170행, B 32,289행)에서: prompt-4 렌더링이 system+user를 글자 단위로 재현하는 행 100%(A 32,170/32,170, B 32,289/32,289), 시스템 프롬프트는 역할마다 1종, assistant 정답이 사후 검증기를 통과하는 행 100%(A는 제약 디코딩용 relax-3 스키마와 키 순서도 100%), 입력 상한 2500자를 넘는 행 0(최대 A 643자, B 417자). 상한 조정은 필요 없다(0.7.4 후보 없음).

## 0.7.3 (미배포) — 2026-10-28 (장애 대응 FAULT_MATRIX A4·B1·B3, 감사 MR-1/2/3/4/9/10/14/15/16)
**깨지는 변경이 있다(운영 기본값을 닫았다).** HTTP 경로·계약 스키마·`serving_config`와 해시는 그대로다(해시 불변 시험 통과).
- **호환 주의(deploy 영향)**: (1) 토큰이 없으면 시작을 거부한다(`MODEL_RUNTIME_TOKENS(_FILE)`; 개발용 `MODEL_RUNTIME_ALLOW_NO_AUTH=1`). 빈 역할·빈 토큰·중복도 거부. (2) `MODEL_RUNTIME_BACKEND=fake`는 `MODEL_RUNTIME_ALLOW_FAKE=1`이 없으면 거부(deploy의 fake 변형 compose에 둘 다 필요). (3) 기본 바인드가 `127.0.0.1`(이미지는 `MODEL_RUNTIME_HOST=0.0.0.0`을 줘서 컨테이너는 그대로). (4) 비인증 `/readyz` 503은 사유를 숨긴다(상태 코드는 그대로이므로 점검 스크립트가 코드만 보면 영향 없고, 사유를 읽던 쪽은 토큰을 붙인다). (5) 입력이 2500자(`MODEL_RUNTIME_MAX_INPUT_CHARS`)를 넘으면 400.
- 요청 총 상한 `MODEL_RUNTIME_REQUEST_DEADLINE_S`(기본 3.5s, **실측 안 한 초기값**): 멈춘 엔진 호출을 무한히 기다리던 문제. 넘으면 503 `ENGINE_TIMEOUT`, 엔진이 돌아오면 자동 복구, 멈춰 있는 동안 `/readyz` 503.
- 엔진 호출 예외 후 `/readyz` 503(다음 성공까지), 단 엔진이 입력을 거부(`ValueError`)한 것은 400이고 `/readyz`에 영향 없음(MR-3). 백엔드 기동 실패는 `MODEL_RUNTIME_BACKEND_RETRY_S`(30s)마다 재시도. `MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S`(120s, 기존 하드코딩 값).
- 인증(MR-1): 역할 `loader`는 PUT만, `MODEL_RUNTIME_PUT_ROLES`로 PUT 역할 제한, 상수 시간 토큰 비교. 연결(MR-4): 소켓 timeout 15s, 동시 연결 32, 동시 적재 2, 실패 항목 16개 보존, 비정상 `Content-Length`·`Transfer-Encoding`·깊은 JSON은 400, 처리 못 한 예외는 내부 정보 없는 503, SIGTERM은 우아한 종료(진행 요청 최대 5s 대기).
- 입력의 채팅 특수 토큰 모양을 깨뜨린다(MR-2, ADR 0011). 번들 정리(MR-9): 내려간 릴리스·실패 적재의 폴더와 시작 시 `.bundle-*` 삭제. 사후 검증기는 `pattern`의 `$`를 `\Z`로 읽는다(MR-14: `Q4.answer\n` 통과 수정; 제약 디코딩용 스키마는 그대로). 문서 불일치 정정(MR-15), 시험의 고정 sleep을 조건 대기로(MR-16).
- ADR: `docs/adr/0001~0012`(D89). 시험: `tests/test_hardening.py` 19건, `FaultCells` 8건.
- 안 한 것: MR-5(공급망 digest·해시 잠금·스캔: CI와 협의 필요), MR-6(실제 모델 지연 실측: GPU 필요), MR-7(엔진 영구 멈춤 재시작: 정책상 `/healthz`는 200이고 밖이 `/readyz`를 본다), MR-11(구조화 로그·메트릭, 보통 이하라 시간 내 못 함), MR-13(respond 대체 문장: 영역 밖 정책 판단).

## 문서 정정 (0.7.2 이후, 코드 변경 없음)
- 설치 줄이 `-e ../boundary-contracts`(형제 폴더 editable)여서 gpu-dev에서 `test_reference_checkout_is_the_installed_tag`가 실패했다(설치된 패키지가 형제 작업 트리 그 자체). 시험은 옳고(설치본이 태그와 같은지 보는 것이 목적) 설치 줄이 공통 규칙(태그 설치)과 어긋났다. README, runbook, design의 설치 줄을 태그 설치로 고쳤다.

## 0.7.2 (미배포) — 2026-10-27 (OPEN_GAPS U16, 정정 흐름)
호환 변경. HTTP API, 계약, `serving_config`와 해시 불변. 기존 흐름 파일(목록 형식, `order`/`slots`/`skips`)의 동작은 그대로다.
- 확인: boundary-contracts v2.0.2 `ActionExtraction`의 action 열거에 `CORRECT`가 있다(NEXT와 같이 reason·targets·note 금지). 0.7.1의 fake는 `CORRECT`를 낼 수 없었다(항상 NEXT). 계약·스키마 변경은 필요 없다.
- 흐름 파일(객체 형식)에 선택 항목 `corrections`를 더했다: `[{"contains": "발화 문구", "key": "Q4.answer", "value": false, "at": "Q5"}]`. 발화에 `contains`가 들어 있으면(`at`이 있으면 입력 question_id가 그 문항일 때만) `{"action":"CORRECT","next_item":...,"values":[{key, value, status:"CORRECTION", save:true, precision:"exact"}]}`를 낸다. `corrections`가 `slots`보다 먼저 적용된다. `next_item`은 turn-engine `validate_extraction` 규칙대로 정정한 문항 바로 다음에 `skips`를 적용한 문항이다(정정한 값이 건너뛰기에 걸리면 그 문항을 건너뛴다). 형식 오류(key 문법, key 문항이 `order`에 없음, value null 등)는 서버 시작 거부(`ValueError` -> `SystemExit`). 새 API: `load_fake_corrections`, `make_default_responder(..., corrections)`, `fake_extraction_for(..., corrections)`.
- 시험: `tests/test_fake_flow.py` `FakeCorrect` 5건(규칙표, at 제한과 불변, turn-engine `validate_extraction` 문항 x 발화 전수, 파일 형식 오류, 서버 환경). 사용법은 `docs/runbook.md`의 `MODEL_RUNTIME_FAKE_FLOW_FILE` 행.
- 미확인: deploy E2E에서 이 경로가 도는지(deploy `test/fake-model/question_flow.json`에 `corrections`를 넣고 정정 발화를 보내야 한다). 칸 키는 이 리포가 모르므로 지어내지 않았다.

## 0.7.1 (미배포) — 2026-10-27 (OPEN_GAPS U16)
호환 변경. HTTP API, 계약, `serving_config`와 그 해시는 바뀌지 않았다(가짜 응답 규칙은 serving_config에 들어가지 않는다. 골든 갱신 없음, `test_serving_config_hash_unchanged`로 확인).
- fake 백엔드가 값을 낸다: `MODEL_RUNTIME_FAKE_FLOW_FILE`을 객체 형식 `{"order","slots","skips"}`로 주면 LORA-A가 입력 문항에 칸 정의(`slots`, type boolean|integer, 첫 칸만)가 있고 발화에서 값을 정할 수 있을 때 NEXT에 ANSWERED 값 하나(`save: true`, `precision: exact`)를 내고, `skips`를 적용한 next_item을 낸다. 규칙: boolean은 발화에 '아니'가 있으면 false, 아니면 true. integer는 발화의 첫 아라비아 숫자열, 숫자가 없고 '아니'나 '없'이 있으면 0, 그 밖은 값 없음. 공백뿐인 발화, 칸 정의가 없는 문항, 값을 정하지 못한 경우는 기존처럼 빈 `values`의 NEXT. 목록 형식 파일은 동작이 그대로다. **E2E에서 이 경로를 쓰려면 deploy의 `test/fake-model/question_flow.json`에 `slots`(와 `skips`)를 넣어야 한다**(칸 키·타입은 이 리포가 모르므로 지어내지 않았다).
- 시험: `tests/test_fake_flow.py` 규칙표(발화 -> 기대 출력 14건, 키 순서 포함), turn-engine `validate_extraction` 통과(DATASET_V2 순서·건너뛰기에서 문항 x 발화 전수, turn-engine이 없으면 건너뜀), 파일 형식 오류.
- `profiles/gpu-12g.json`의 `status.loadable`을 `measured`로(2026-10-27 gpu-dev 1회 측정, OPEN_GAPS U01: 약 10.1GB, KV 5.42GiB). `engine`, `base_model`, `serving_config` 해시에 영향 없음(status는 해시 입력이 아님). `two_adapters_loaded_together`는 간접 확인뿐이라 `unverified`로 둔다.
- `scripts/make_neutral_package.py` 도움말의 낡은 모델 표기(Qwen3-4B -> Qwen3-1.7B)를 고쳤다.

## 0.7.0 (미배포) — 2026-10-27 (D84, 릴리스 열차)
묶음 릴리스(연쇄 정렬을 줄이기 위해 한 번에 낸다). **깨지는 변경이 있다.**
- **깨지는 변경**: `prompt-3` 제거(G10). `/v1/info`의 `serving_config_expectation.prompt_template_versions`가 `["prompt-3","prompt-4"]`에서 `["prompt-4"]`로 줄고(호환 변경으로 보이지만 `prompt-3`을 쓰는 `serving_config`는 이제 `UnsupportedServingConfig`로 거부되므로 **깨지는 변경**으로 적는다), `deprecated_prompt_template_versions`는 빈 목록이다(필드는 유지). 0.6.x로 만든 릴리스 중 `prompt_template_version: prompt-3`인 것은 적재되지 않는다. 매니페스트·해시를 `prompt-4`로 다시 만들어 제출해야 한다. 서버에서 학습한 어댑터(dataset_10_14)는 원래 `prompt-4`다. `model_runtime_version`의 MAJOR.MINOR가 0.7이어야 하므로 0.6.x 매니페스트는 어차피 다시 만들어야 한다.
- **비밀 파일화(D84)**: `MODEL_RUNTIME_TOKENS`, `MODEL_RUNTIME_RELEASE_TOKEN`을 `boundary_contracts.read_secret`으로 읽는다. `<이름>_FILE`(파일 경로)이 우선이고 없으면 환경 변수(개발·시험용 호환, 기존 설정은 그대로 동작). 파일을 읽지 못하면 시작을 거부하며 메시지에는 이름과 경로만 있다. 시작 로그 한 줄에 값이 아니라 출처(file/env/unset)만 남긴다. HTTP 모양(`/readyz`, `/v1/info`)은 바꾸지 않았다(출처 필드를 넣지 않음). 시험: `tests/test_secrets_hidden.py`(값이 로그·`/readyz`·`/v1/info`·401 응답·예외 메시지에 없음).
- boundary-contracts 태그 v2.0.1 -> v2.0.2(Dockerfile, smoke.sh, gpu_check.sh, pyproject, 문서).
- `tests/golden/`(요구 작성자의 정적 예시 216개, G09)를 제거했다. 어떤 시험·스크립트도 읽지 않는 것을 grep으로 확인했다(`tests/prompt_golden/`은 다른 것이며 남는다). `prompt-3` 골든 파일 2개도 제거.
- `pyproject.toml`의 dev extra에서 turn-engine 하드 핀을 없앴다(U15). 근본 원인: turn-engine 버전이 여기·deploy·STATUS 등 여러 곳에 중복되어 한쪽만 올리면 어긋났다. turn-engine이 필요한 시험은 `pytest.importorskip`으로 없으면 건너뛴다. 어떤 turn-engine을 쓸지는 `docs/runbook.md`의 설치 방법.
- runbook에 'LoRA 교체' 절차를 추가했다. 어댑터와 프로파일의 기반 모델이 다르면 엔진 전에 거부되는 동작은 기존 시험이 보장한다(`test_base_model_mismatch_rejected_before_engine`).

## 0.6.5 (미배포) — 2026-10-27 (D83)
완화 정책이 바뀌어 `serving_config`의 `constraint`와 해시가 바뀐다(계약 2.0.0 그대로, HTTP API 그대로). 계약 스키마 원본은 바꾸지 않는다.
- **relax-3**: `relax()`가 `allOf`의 `if/then(/else)`를 같은 뜻의 `anyOf` 변형으로 펼쳐 제약 디코딩에 준다(코드가 원본에서 기계적으로 만든다. 사본 스키마 없음). `ActionExtraction`은 최상위 8개 규칙이 action 변형 8개(NEXT, CORRECT, REASK, CONFIRM, HOLD, SKIP, REDIRECT+note, REDIRECT-note)로, values 항목 2개 규칙이 status 변형 3개(ANSWERED·CORRECTION / MISSING·UNCLEAR·UNKNOWN·REJECTED / CONFLICT)로 펼쳐진다. action별 reason 허용값, NEXT·CORRECT의 reason·targets·note 금지, REASK 등의 reason·targets 필수, status별 save·precision·value 규칙이 문법에 들어간다.
  - 지원 모양: `if`가 한 속성의 const/enum 검사이고(`required`는 그 속성 하나이거나 없음), 그 속성이 기본 스키마에 유한 값 집합(enum/const)을 가진 규칙. `then`/`else`는 `properties`·`required`만. 규칙마다 가지(if 참/거짓)를 만들고 곱한 뒤 모순 변형을 버린다. 부정은 기본 enum의 여집합으로 만든다.
  - 못 펼치는 규칙은 그 규칙만 빼고 `CallInfo.relaxed`에 `allOf@<경로>[<번호>]:<이유>`로 남긴다. 변형이 `MAX_VARIANTS`(64)를 넘으면 그 객체의 규칙을 모두 빼고 `allOf@<경로>:variants>64`. 펼친 성공은 `allOf->anyOf`. `allOf`/`anyOf`/`enum`이 지원되지 않는 백엔드는 예전처럼 `allOf`를 빼고 `allOf`로 기록한다.
  - `DEFAULT_SUPPORTED_FEATURES`에 `maxItems`를 더했다(REDIRECT의 values 빈 목록 규칙). xgrammar 0.2.7에서 강제되는 것을 CPU에서 확인했다.
  - 사후 검증(원본 계약 스키마)은 그대로다.
- **호환(결정)**: `relax-2` `serving_config`는 받지 않는다(`UnsupportedServingConfig`, relax-2 때와 같다). **매니페스트의 `serving_config.constraint`(`relax_policy_version: "relax-3"`, `supported_features`에 `maxItems` 포함)와 `serving_config_hash`를 새로 만들어 릴리스에 다시 제출해야 한다.** fake의 해시도 바뀌었다(정책 문자열만).
- 시험: `tests/test_anyof_constraint.py`(원본과 변환 스키마의 판정 등가: 전수 조합 1764건 + status·precision·save·value 168건 + 규칙 위반 변이 33건 + 유효 예시 5건, 한도·미지원 모양·폭발 상한). `scripts/xgrammar_check.py`(xgrammar 0.2.7로 컴파일해 1970건 수락·거부).
- 확인(CPU): 변환 스키마는 xgrammar 0.2.7에서 경고 없이 컴파일되고, 유효 75건 수락·위반 1895건 거부(불일치 0). 원본 스키마는 `allOf with multiple options` 경고를 내고 위반 1895건을 모두 수락한다.
- 미확인: 실제 vLLM 0.31.0·Qwen3·어댑터에서의 동작, 지연·컴파일 시간(변형 8개로 문법이 커진다), 모델이 재시도 없이 통과하는 비율. **키 순서(해결)**: xgrammar는 `properties` 순서대로 키를 내도록 강제하는데 v2.0.0 스키마는 사전순이 아니어서 사전순 출력이 거부됐다(기존 문제, 사전순 예시 거부로 확인). 계약 v2.0.1이 `properties`를 사전순으로 고쳤고(최상위 action,next_item,note,reason,targets,values / values 항목 key,precision,save,status,value) 이 리포는 v2.0.1로 올렸다(Dockerfile, smoke.sh, gpu_check.sh, pyproject, 문서, 시험의 설치 버전, venv). **relax는 `properties` 순서를 건드리지 않는다. 순서의 정본은 계약이다.** CPU에서 xgrammar 0.2.7로 확인: 사전순 예시 5/5 수락, 거꾸로 직렬화한 예시 5/5 거부, 거꾸로 직렬화한 유효 사례 75/75 거부(`scripts/xgrammar_check.py`), 정식 시험 `tests/test_key_order.py`(xgrammar가 없으면 그 한 건만 건너뜀).

## 0.6.4 (미배포) — 2026-10-27 (D82)
완화 정책이 바뀌어 `serving_config`의 `constraint`와 해시가 바뀐다(계약 2.0.0 그대로, HTTP API 모양 그대로).
- **relax-2**: `DEFAULT_SUPPORTED_FEATURES`에 `pattern`, `minItems`를 더했다. `RELAX_POLICY_VERSION`은 `relax-2`. gpu-dev 실측에서 vLLM 0.31.0/xgrammar 0.2.7이 `pattern`을 실제로 강제하는데(컨테이너 안 직접 시험, 헤드 확인) relax-1이 `pattern`·`minItems`를 빼서 ActionExtraction의 `key`·`targets`·`next_item` 문법이 제약되지 않아 LoRA-A extract가 100% OUTPUT_INVALID였다. `allOf`는 xgrammar가 부분 지원이라 계속 뺀다. `minItems`는 이 리포에서 따로 강제 여부를 확인하지 못했다.
- **호환(결정)**: `relax-1`을 가진 `serving_config`는 받지 않는다(`UnsupportedServingConfig`). 지원 키워드 집합이 달라 어차피 어긋나므로 두 정책을 함께 받지 않는다. **매니페스트의 `serving_config.constraint`(`relax_policy_version: "relax-2"`, `supported_features`에 `pattern`·`minItems` 포함)와 `serving_config_hash`를 새로 만들어 릴리스에 다시 제출해야 한다.** 가짜 백엔드의 `fake_serving_config()` 해시도 바뀌었다(`FakeBackend` 지원 집합은 그대로, 정책 문자열만 relax-2).
- OUTPUT_INVALID(503)의 `details`에 `violations`를 더했다: 마지막 시도에서 어긴 규칙의 `"<경로>:<키워드>"` 목록(예 `/values/0/key:pattern`, JSON이 아니면 `json`). 정렬·중복 없음·최대 20개. 같은 목록을 로그(WARNING)에 낸다. 발화·모델 원문·값은 내지 않는다. `BoundaryError.details`가 자유 객체(`type: object`)라 계약 스키마 변경은 필요 없고 `details.reason`·`attempts`는 그대로다. HTTP.md에는 `violations`가 문서화되어 있지 않으므로 헤드가 반영할지 정한다(선택 필드, 호출자는 무시해도 된다).
- 시험: relax·지원 집합 기대값, 골든 해시, OUTPUT_INVALID details/로그 시험(원문 비노출 포함).
- 미확인: Qwen3와 실제 어댑터에서 relax-2로 extract가 통과하는지, `allOf` 의미 규칙(NEXT의 reason 금지, REASK의 reason enum)을 모델이 지키는지(추정으로 못 지킬 수 있다, D82).

## 0.6.3 (미배포) — 2026-10-27 (D81)
호환 변경(계약 2.0.0 그대로, 서버 동작·API 변화 없음). 프로파일 내용만 바뀐다.
- `profiles/gpu-12g.json`의 기반 모델을 Qwen3-4B에서 **Qwen3-1.7B**로 바꿨다. 파이프라인 데모용(`status.for: pipeline-demo`)이며 학습된 4B/8B 어댑터는 24GB급 이상(`rtx4090-24g`)에서 서빙한다. engine은 이전 gpu-12g(Qwen2.5-1.5B, 0.8/8192/8, 적재 성공, 약 10.2GB)에서 출발해 보수적으로 둔 추정(0.8, max_model_len 4096, max_num_seqs 4, max_lora_rank 16). **적재 가능 여부는 미확인**(`status.loadable: unverified`). 어댑터·프로파일 기반 모델이 다르면 412로 거부하는 규칙은 그대로다.
- `make_neutral_package.py`의 `--model-config` 요구는 그대로. 런북에 Qwen3-1.7B 차원을 HF `config.json`에서 얻는다고 적었다(수치는 넣지 않음).
- 시험의 프로파일 기대값을 갱신했다.

## 0.6.2 (미배포) — 2026-10-27 (deploy CR-D22 v2 1항·4항)
호환 변경(계약 2.0.0 그대로). 설정 없이 쓰던 fake의 출력은 그대로다.
- fake 백엔드의 LORA-A 출력을 입력에 맞게 낼 수 있다. `MODEL_RUNTIME_FAKE_FLOW_FILE`(문항 ID 목록 JSON, 또는 `{"order": [...]}`)을 주면 입력 `question_id`의 다음 문항으로 가는 `{"action":"NEXT","next_item":<다음 문항|END>,"values":[]}`를 낸다. `MODEL_RUNTIME_FAKE_EXTRACTION`(고정 출력 JSON)은 flow보다 우선한다. 둘 다 없으면 이전과 같은 고정 출력(`next_item: END`)이다. 설정 오류는 시작 거부.
  - 근거: values가 비면 저장 값이 없어 건너뛰기 규칙이 적용되지 않으므로 turn-engine v0.3.0 `validate_extraction`의 기대 `next_item`은 순서상 바로 다음 문항(마지막이면 END)이다. model-runtime은 문항 순서를 모르므로 설정으로 받는다. 시험에서 `DATASET_V2_FLOW` 25개 문항 모두 위반 0건을 확인했다.
  - `GenerationRequest.input`(선택, 기본 None) 추가: 가짜 백엔드가 입력의 문항을 읽는 데만 쓴다. 파이썬 API: `make_default_responder(order, extraction)`, `fake_extraction_for`, `load_fake_flow`.
- `scripts/make_neutral_package.py`: `--system-prompt-a FILE`, `--system-prompt-b FILE`. 파일 바이트를 그대로 `system_prompt.txt`로 넣고 `manifest.system_prompt`의 경로·해시와 `adapter_hash`가 그 내용으로 만들어진다. 비어 있거나 UTF-8이 아니면 거부. 주지 않으면 자리표시 문장.
- 시험용 의존 extras `dev`(pytest, turn-engine v0.3.0). 시험 133개(+10).

## 0.6.1 (미배포) — 2026-10-27 (D77)
호환 변경(계약 2.0.0 그대로, 0.6.0 릴리스도 적재 가능). 0.6.x MAJOR.MINOR가 같아 `model_runtime_version` 검사를 통과한다.
- 프롬프트 템플릿 **`prompt-4`** 추가·기본값 승격: user 메시지를 학습 데이터와 글자 단위로 같게 `json.dumps(obj, ensure_ascii=False)`로 쓴다. LORA_A는 최상위 키만 정렬하고 `state` 안은 호출자 순서 보존(survey-service가 학습 규칙대로 만든다), LORA_B는 전 수준 정렬. 채팅 형식은 prompt-3과 같다.
- `prompt-3`(JCS)은 호환용으로 남기고 폐기 예정으로 표시(`DEPRECATED_TEMPLATE_VERSIONS`). 제거는 하지 않았다: 0.6.0으로 만든 릴리스가 있을 수 있고 제거는 그 적재를 깨기 때문이다. 다음 MINOR에서 제거를 제안한다.
- `fake_serving_config()`의 기본이 `prompt-4`. `/v1/info`의 `serving_config_expectation`에 `prompt_template_version_default`, `deprecated_prompt_template_versions` 추가(`prompt_template_versions`는 두 버전).
- 점검 스크립트 `scripts/check_prompt_matches_dataset.py`(stdin jsonl 대조), `scripts/check_chat_template.py`(chat_template 렌더 대조). 시험 123개(+14).

## 0.6.0 (미배포) — 2026-10-27 (계약 2.0.0, D74·D75)
**깨지는 변경(MINOR이지만 0.x의 API 변경).** 지시: head/docs/migration-2.0.0.md. 0.5.x로 만든 릴리스와 `serving_config`는 거부된다.
- I9 라이브러리: `parse`/`generate` -> `extract`/`respond`, `parse_raw`/`generate_raw` -> `extract_raw`/`respond_raw`. `respond`는 `ResponseResult(text, is_fallback)`. extract가 재시도 후에도 `ActionExtraction`을 못 지키면 **`ModelOutputInvalid`**(D75; 대체 출력 없음). 0.2.0에서 삭제했던 이름이지만 의미가 다르다(출력 보호 예외가 아니라 값을 지어내지 않는 실패).
- 모델 입출력 스키마는 `boundary_contracts`(태그 v2.0.0)에서 읽는다. `tests/fixtures/schema`의 모델 스키마 복사본을 삭제했다(`CONTRACT_VERSION`만 남김). `schema_dir`는 문진 스키마 정수 버전 확인용.
- 시스템 프롬프트는 모델 패키지의 파일(D75): `load(..., system_prompts=)`(계약 I9 시그니처에 없는 추가 인자, CR-004), `render_prompt(..., system_prompt=)`, `SystemPromptMismatch`(서버는 422 `INTEGRITY_ERROR`), `session.system_prompt_hashes`, `/v1/info`의 `system_prompt_sha256`.
- 프롬프트 템플릿 `prompt-3`(Qwen3 ChatML, `enable_thinking=False`, 입력 JSON은 JCS). `prompt-2` 삭제. 문장 본문은 코드에 없다.
- 보호 정책 `protect-2`: respond 형식(비어 있지 않은 문장)과 재료를 이은 대체 문장. v1 파서 보호 P1~P6, 생성기 G1·G4 삭제. `protect-1` 삭제. 보호 이름 `G0`, `G5`.
- LoRA-B는 제약 디코딩을 쓰지 않는다(텍스트). 제약 스키마에서 `$comment`를 뺀다.
- 서버: `POST /v1/releases/{id}/extract`(JSON), `.../respond`(`text/plain`), 503 `UNAVAILABLE` `details.reason=OUTPUT_INVALID`(`attempts`), `X-Openit-Protections`는 있을 때만. `/v1/parse`·`generate` 경로 삭제. `MODEL_RUNTIME_FAKE_RISK_PHRASE`와 `FakeBackend(risk_phrase=)` 삭제(설정이 남아 있으면 시작 거부).
- 프로파일: `gpu-12g`=Qwen3-4B, `rtx4090-24g`=Qwen3-8B(`status.loadable`=unverified). `gpu-dev-12g`는 파이프라인 시험 전용 표시. 중립 패키지 생성기에 시스템 프롬프트 파일, `head_dim`, B의 `constraint_mode: none`.
- Dockerfile 라벨: 계약 `v2.0.0`, `io.openit.model-runtime=0.6.0`. smoke.sh는 형제 계약 폴더가 태그 v2.0.0이 아니면 중단하고 라벨을 확인한다. gpu_check.sh도 같다. GPU 스크립트(`gpu_probe.py`, `gpu_serve_check.py`)를 v2 API에 맞췄으나 GPU가 없어 `--mock`으로 논리만 확인했다.
- 시험: v2로 교체하고 계약의 `HttpModelRuntime`으로 서버 적합성 시험 추가.

## 0.5.3 (미배포) — 2026-10-26
- Dockerfile: `ARG REVISION`·버전 `ARG`와 `LABEL` 블록을 맨 뒤(USER 앞)로 옮겼다. 커밋마다 바뀌는 값이 앞에 있으면 무거운 pip 레이어 캐시가 매번 깨진다. `REVISION=aaaa` 뒤 `REVISION=bbbb`(+`BOUNDARY_VERSION` 변경)로 CPU 변형을 다시 빌드해 pip 레이어가 `CACHED`임을 확인. 레이어 순서 시험 갱신.
- GPU 변형 이미지에 C 컴파일러(`gcc`, `libc6-dev`)와 `CC=gcc` 추가: Triton이 LoRA 커널 등에서 CUDA 유틸 모듈을 C 소스로 빌드해 `Failed to find C compiler`로 실패하던 것. 레이어는 무거운 pip 레이어 뒤·USER 앞(빌드 캐시 재사용). Python 헤더는 python 이미지에 이미 있다.
- 이미지 ENV 기본값: `HOME`, `XDG_CACHE_HOME`, `VLLM_CACHE_ROOT`, `TORCHINDUCTOR_CACHE_DIR`, `TRITON_CACHE_DIR`, `TORCH_HOME`(모두 `/var/lib/model-runtime` 아래), 원격 측정 차단(`VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`, `HF_HUB_DISABLE_TELEMETRY=1`). 서버가 시작할 때 이 디렉터리를 `mkdir -p`로 보장하고 쓸 수 없으면 `/readyz` 503 reason `runtime dir not writable: <경로> (uid <n>)`.
- 0.5.2 이후의 HF 캐시 쓰기 검사(`hf cache not writable: ...`)를 이 버전에 포함. 시험 118개.

## 0.5.2 (미배포) — 2026-10-26
- (버전 변경 없이 추가) 시작 시 HF 캐시 쓰기 검사: 불가하면 `/readyz` 503 reason `hf cache not writable: <경로> (uid <n>)`(vllm·온라인일 때만). 런북에 마운트 경로 주의.
- `scripts/make_neutral_package.py`: 학습 없는 중립 LoRA(lora_B=0, PEFT 형식) 모델 패키지(ModelPackageManifest, backend vllm)를 결정적으로 생성(표준 라이브러리만). `scripts/gpu_neutral_check.py`.
- 프로파일 `gpu-12g.json`(12GB급 이하, 표준 이름). `MODEL_RUNTIME_BASE_MODEL`, 이미지의 `HF_HOME`(uid 10001 소유 디렉터리).
- `/readyz`가 I4(`MODEL_RUNTIME_RELEASE_URL`) 도달성을 확인(안 닿거나 미설정이면 503과 사유, `/healthz`는 200). 위치 독립 점검.
- 런북: GPU 표준 컨테이너 실행, 오프라인 모델 캐시, 단독 실행 compose와 네트워크 조건표. 시험 114개.

## 0.5.1 (미배포) — 2026-10-15 (deploy CR-D17)
- 번들 캐시 기본 경로를 `/var/lib/model-runtime/bundles`(이미지에서 uid 10001 소유)로 바꿨다. 쓸 수 없으면 `/readyz` 503과 이유, `PUT` 503. 새 이름 있는 볼륨 마운트 확인.
- `FakeBackend(risk_phrase=)`, 환경 변수 `MODEL_RUNTIME_FAKE_RISK_PHRASE`(fake 전용, 기본 꺼짐, vllm이면 시작 거부): 해당 문구가 든 발화에 `RISK_SIGNAL` 항목을 낸다(출력 계약 통과, 보호 없음).
- `/v1/info`에 `serving_config_expectation`, `test_hooks` 추가. 이미지에 스키마를 넣는 선택 옵션(`--build-context schema=`, `/opt/survey-schema`), 라벨로 스키마 버전 표시. 시험 105개.

## 0.5.0 (미배포) — 2026-10-15
- 추론 서버 `python -m model_runtime.server`와 이미지(`Dockerfile`, CPU/fake·GPU 두 변형). 경로는 `boundary-contracts/HTTP.md` I9 절(릴리스 단위 `PUT`/`GET /v1/releases/{id}`, `POST .../parse|generate`, `/v1/info`, `/healthz`, `/readyz`), 보호 결과는 `X-Openit-Outcome|Attempts|Protections`. 적재는 I4 `HttpReleaseReader`로 서버가 스스로 하고 실패는 그 릴리스에만 기록한다. 대기가 길면 503. SIGTERM에서 엔진을 내린다.
- `decoding.retry_temperature`(선택): 시도 1 이상의 온도. 없으면 기존 동작이며 기존 해시는 그대로다.
- `VLLMBackend(None, ...)`로 엔진만 올리고 `bind(adapters)`로 릴리스별 어댑터 쌍을 묶는다. `close()` 추가.
- 프로파일 `rtx4090-24g.json`(잠정, 미시험), 잠금 파일 `requirements-{cpu,gpu}.lock`, `scripts/smoke.sh`, `scripts/gpu_serve_check.py`. I9(라이브러리) 시그니처 변경 없음. 시험 100개.
- 제안(survey-service가 정할 일): 이미지 라벨에 vLLM·xgrammar 버전도 남긴다.

## 0.4.0 — 2026-10-15
- I9 시그니처 변경 없음. 실제 백엔드 `VLLMBackend`(vLLM 0.31.0 + 구조화 출력 + LoRA, `pip install .[vllm]`, 지연 import라 없으면 `BackendNotAvailable`)와 어댑터 사전 검사(순위 r > `max_lora_rank`, 기반 모델 불일치는 `UnsupportedServingConfig`, PEFT 형식 아님은 `ContractViolation`)로 MINOR를 올렸다.
- `load(..., backend=None)`은 모든 역할의 `constraint.backend`가 `"vllm"`이면 `VLLMBackend(adapters)`를 만든다. `load_profile`, `DEFAULT_SUPPORTED_FEATURES`, 프로파일 `gpu-dev-12g.json`, GPU 시험 스크립트, 변경 요청 002.

## 0.3.0 (미배포) — 2026-10-15
- **API 변경(D45)**: `load(adapters, serving_configs, *, schema_dir, expected_serving_config_hashes, backend=None)`. 설정과 기대 해시가 역할(LORA_A, LORA_B)별이다. 해시·지원 여부 검증은 역할별이며 `ServingConfigMismatch.role`에 어느 역할인지 담는다. 어댑터·설정·기대 해시의 역할 집합이 다르면 `ContractViolation`.
- 재시도 상한·시드·디코딩·프롬프트 버전·보호 정책을 역할별 설정으로 적용한다. 두 역할은 한 백엔드를 공유한다.
- `ModelSession.serving_config_hash` → `serving_config_hashes`(역할별). `FakeModelSession(serving_configs, *, schema_dir, expected_serving_config_hashes, backend=None)`. `fake_serving_configs` 추가.
- `render_prompt`·`serving_config_hash`·`validate_serving_config`는 변경 없음.
- 0.2.0 사용 코드(survey-service)는 `load` 호출과 `serving_config_hash` 속성 사용을 고쳐야 한다.

## 0.2.0 (미배포) — 2026-10-15
- 출력 보호(D36): `parse`/`generate`는 재시도 뒤에도 유효하지 않으면 예외 대신 규칙이 정한 대체 출력을 낸다. 파서 P1~P6, 생성기 G1·G4·G5(`docs/rules.md`). **`ModelOutputInvalid` 삭제.**
- 생성기 모델은 `response_text`만 낸다. `is_fallback`은 코드가 채운다.
- `parse_raw`/`generate_raw`(`RawResult`), `last_call_info`에 `outcome`·`protections`.
- `schema_dir`는 `load`·`render_prompt`의 필수 키워드 인자이고 항상 `.../schema/schemas`. `configure`·`SchemaDirRequired` 삭제.
- `serving_config`에 필수 키 `protection_policy_version`(`protect-1`) 추가. **이전 0.1.0의 `serving_config` 해시와 호환되지 않는다**(미배포).
- 프롬프트 `prompt-1` 삭제, `prompt-2` 추가(생성기 규칙 1~4, 파서 어휘 목록).
- 개발 환경: `.venv`, pytest.
- 보호 정책을 `ProtectionPolicy`로 분리(`protect-1`): 재료가 없을 때의 대체 문구(잠정)와 판단 J1~J3을 정책 버전에 묶음. `fallback_text(..., policy_version=)`.

## 0.1.0 (미배포)
- I9 호출 경로: `load`, `parse`/`generate`, `render_prompt`, `serving_config_hash`, `validate_serving_config`.
- 세 겹 출력 보장(제약 디코딩 백엔드, 항상 거치는 사후 검증, `max_attempts` 재시도, 시드 = 기본 시드 + 시도 번호).
- `FakeModelSession`/`FakeBackend`.
- 해시는 `boundary_contracts.canonical` 사용(자체 JCS 삭제).
