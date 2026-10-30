# model-runtime

문진 챗봇이 쓰는 두 개의 작은 언어 모델(LoRA-A 추출, LoRA-B 응답)을 불러와 같은 방식으로 호출해 주는 공유 라이브러리이자 추론 서버다.

환자 발화에서 "무슨 뜻인지"를 구조화해 뽑는 일(A, `extract`)과, 정해진 재료로 환자에게 할 문장을 만드는 일(B, `respond`)을 맡는다. 학습 후 평가, 승인 평가, 실제 서비스가 같은 프롬프트·디코딩으로 모델을 부르게 하는 것이 목적이다(docs/brief.md). 하지 않는 것: 학습(model-lab), 모델 보관과 승인(release-gate), 서비스 서버(survey-service), 판단 로직(문진 서비스). 모델이 판단하게 만드는 프롬프트도 두지 않는다. 출처: docs/brief.md.

## 전체 속의 위치

| 이웃 | 방향 | 무엇을 | 인터페이스 |
| --- | --- | --- | --- |
| survey-service | 받음/줌 | 호출 요청을 받고 `ActionExtraction`(A) 또는 문장(B)을 돌려줌 | I9, HTTP.md I9: `POST /v1/releases/{id}/extract`, `.../respond` |
| release-gate | 받음 | 승인된 릴리스 번들(어댑터, 설정)을 읽음 | I4(`HttpReleaseReader`), `PUT /v1/releases/{id}`로 적재 |
| model-lab | 받음 | 학습된 어댑터와 모델 패키지(매니페스트) | 어댑터 디렉터리, `ModelPackageManifest` |
| boundary-contracts | 의존 | 입출력 스키마, 해시, `read_secret` | 기준 태그는 `deploy/scripts/lib.sh` (docs/boundary.md) |
| 문진 스키마 | 의존 | 정수 버전 1 확인 | `schema_dir` 읽기 전용 |

호출자가 입력 JSON의 키 순서를 지켜야 하는 등 규칙은 docs/boundary.md "호출자가 지켜야 할 것"에 있다.

## 구조

| 경로 | 역할 |
| --- | --- |
| `schemas/`, `schemas/examples/` | 영역 내부 타입의 JSON Schema와 통과·거부 예시, "스키마 밖 조건" 표(`schemas/README.md`) |
| `src/model_runtime/types/` | `schemas/`에서 생성한 코드 타입(손으로 고치지 않는다, 생성 명령은 아래 "실행 방법") |
| `src/model_runtime/components/` | 컴포넌트(파일 하나에 업무 하나). 프롬프트 조립, 입출력 검증, 제약 스키마 완화, 서빙 설정 검사, 재시도, LoRA-A·B 역할, 가짜·모방·vLLM 백엔드, 릴리스 레지스트리, 통신(`link_receive`, `link_release_fetch`). 표는 `docs/components.md` |
| `src/model_runtime/wiring/` | 컴포넌트 연결과 주입: `load`·세션, 스키마 문지기, 서비스 조립 |
| `src/model_runtime/server.py` | 진입점과 환경 변수 읽기(컴포넌트 아님) |
| `src/model_runtime/__init__.py`, `prompts/`, `schema_guard/` | 공개 API(다른 영역이 import하는 이름)를 새 위치에서 다시 내보낸다 |
| `profiles/` | 하드웨어별 설정: `gpu-12g.json`, `rtx4090-24g.json`, `gpu-dev-12g.json`(Qwen2.5-1.5B, 중립 어댑터·파이프라인 시험 전용, 파일의 `status`) |
| `tests/`, `scripts/` | 시험(가짜 백엔드·vLLM 모킹, 구조 검사 `tests/test_structure.py` 포함), 스모크와 GPU 점검 스크립트 |
| `Dockerfile`, `requirements-{cpu,gpu}.lock` | 이미지 두 변형(CPU/fake, GPU/vLLM)과 잠금 파일 |

```mermaid
flowchart LR
  S[survey-service] -->|extract / respond| M[model-runtime 서버]
  R[release-gate I4] -->|번들 읽기| M
  M --> P[프롬프트 조립 render_prompt]
  P --> A[LoRA-A: JSON, 제약 디코딩 relax-3]
  P --> B[LoRA-B: 텍스트, 제약 없음]
  A -->|ActionExtraction 또는 503 OUTPUT_INVALID| S
  B -->|문장 또는 대체 문장| S
```

제약 디코딩 `relax-3`은 A의 출력 스키마를 xgrammar가 받을 수 있게 같은 뜻의 `anyOf`로 펼친 방식이다(docs/STATUS.md 19차). 프로파일: `gpu-12g`는 Qwen3-1.7B(파이프라인 데모용), `rtx4090-24g`는 Qwen3-8B(docs/boundary.md).

## 실행 방법

전제: 파이썬 3(venv), 시험만 할 때는 GPU 불필요. 이미지 빌드·스모크는 Docker가 필요하다. 계약 소스는 태그 기준이며, 형제 폴더 `../boundary-contracts`가 있으면 거기서 태그를 꺼내고 없으면 GitHub에서 태그를 clone한다(GitHub SSH 접근 필요). 이 리포가 쓰는 계약 태그의 정본은 `Dockerfile`의 `ARG BOUNDARY_VERSION`이다(워크스페이스 기준표 `deploy/scripts/lib.sh`와 같아야 하며, 다르면 이 파일을 고친다). GPU 실행은 NVIDIA 드라이버와 `--gpus`가 필요하다. 비밀은 값이 아니라 파일 경로(`MODEL_RUNTIME_TOKENS_FILE`, `MODEL_RUNTIME_RELEASE_TOKEN_FILE`)로 받는다(D84, docs/runbook.md).

```bash
# 1) 설치: 정본 스크립트. .venv를 만들거나 재사용하고, Dockerfile의 계약 태그로 boundary-contracts·pytest·jsonschema·-e .를 맞춘다(멱등, 형제 폴더 불필요)
bash scripts/setup-env.sh
# 2) 시험
.venv/bin/python -m pytest -q
# 2-1) 코드 타입 생성(schemas/ -> src/model_runtime/types/, datamodel-code-generator는 requirements-codegen.lock으로 고정). 손으로 고치지 않는다
bash scripts/gen-types.sh
# 3) 이미지 빌드와 스모크 (CPU/fake, 빈 포트를 Docker가 배정)
scripts/smoke.sh
# 4) 서버 (이미지 없이). 스키마 경로가 필요하다
MODEL_RUNTIME_SCHEMA_DIR=<.../survey-service/schema/schemas> MODEL_RUNTIME_BACKEND=fake \
  MODEL_RUNTIME_RELEASE_URL=<release-gate 주소> .venv/bin/python -m model_runtime.server
```

`setup-env.sh`는 xgrammar를 설치하지 않는다(무거운 선택 의존이라 그것이 필요한 시험이 건너뛰어지는 것은 의도된 선택이다). turn-engine은 쓰지 않는다(0.9.0에서 의존을 없앴고 3.0.0에서 표지도 action_rules_version으로 바꿨다). 문항 흐름표는 문진 스키마에서 읽는다(docs/runbook.md). 또 형제 `../boundary-contracts`가 없으면 `test_reference_checkout_is_the_installed_tag` 1건이 건너뛰어진다(참조 서버·예제는 설치된 태그 패키지에서 읽는다). 이것이 기준이고, 몇 건이 건너뛰어지는지와 의존을 더하면 몇 건이 더 도는지는 "현재 상태" 절의 시험 상태 항목이 정본이다.

이 세션에서 실제로 돌린 것: `.venv/bin/python -m pytest --collect-only -q`(수집 개수는 "현재 상태" 절), `.venv/bin/python -m model_runtime.server --help`(스키마 경로가 필요하다는 안내가 나왔고 서버는 뜨지 않음). 돌리지 않은 것: 시험 실행, `scripts/smoke.sh`, 이미지 빌드, 서버 기동, GPU 관련 전부. 4번의 환경 변수 조합은 runbook 표를 옮긴 것이며 직접 기동해 확인하지 않았다.

## 현재 상태

현행 버전은 `src/model_runtime/session.py`의 `RUNTIME_VERSION`(이력은 CHANGELOG, 마지막 STATUS 기록은 docs/STATUS.md). 확인된 것:
- 14차(0.6.0) 시험 109 통과/0 실패, CPU 이미지 빌드와 스모크 통과(`/healthz`, `/readyz`, 라벨, non-root). 123 통과는 15차(0.6.1)의 값이다(docs/STATUS.md).
- **시험 상태(정본, 개수는 이 한 곳)**: `bash scripts/setup-env.sh` 뒤 `.venv/bin/python -m pytest -q -rs`(= setup-env.sh가 선언하는 test_command): **336 통과, 2 건너뜀**(xgrammar 없음 1, 참조 데이터 경로 없음 1), 계약 v3.0.0, 이 개발 PC(gpu-dev) 한 환경에서 잰 값이다. 깨끗한 두 번째 환경에서는 재현하지 않았다. 이전 판의 개수는 docs/CHANGELOG.md·docs/STATUS.md에 남아 있다.
- relax-3: 원본/변환 스키마 판정 등가, xgrammar 컴파일 경고 없음(CPU, STATUS 19차). gpu-dev에서 extract 48회 모두 형식 통과(OPEN_GAPS U04, 헤드 기록).
- gpu-dev 12GB에서 Qwen3-1.7B 적재, 약 10.1GB 사용(OPEN_GAPS U01, 헤드 기록).

확인 못 한 것(문서 기준): 학습된 4B/8B 어댑터의 GPU 적재, `rtx4090-24g`의 Qwen3-8B 적재, 실제 어댑터를 GPU에서 얹는 절차(STATUS 20차). 이 세션에서는 위 숫자를 다시 재현하지 않았다.

## 이 영역의 열린 항목 (head/docs/OPEN_GAPS.md, 읽기 전용으로 확인)

| ID | 내용 | 막는 것 |
| --- | --- | --- |
| G04 | respond 대체 문장의 재료 없음 문구가 잠정 | 사용자(도메인 소유자) 확인 |
| G05 | v1 G1(응답 숫자 검사)에 해당하는 검사를 둘지 미정 | 결정 필요(담당 표기 없음) |
| G09 | model-lab의 `tests/golden/`가 남아 있음(이 영역은 제거 완료) | 다른 영역(model-lab) |
| U01 | 12GB에서 A·B 동시 부하와 장시간 안정성, 프로파일 값 반영 | GPU 실측 |
| U03 | 서빙 프롬프트와 학습 글자 일치: 토큰 ID 대조, 채팅 템플릿 설정, `<think>` 처리 | 다른 영역(학습 서버 코드)·GPU |
| U04 | 제약 디코딩의 컴파일 비용, `minItems`/`maxItems` 강제 여부 | GPU 실측 |

## 설계 결정 기록 (ADR, D89)

구조 선택의 이유는 `docs/adr/`에 한 파일에 결정 하나로 둔다. 근거를 모르는 것은 '근거 기록 없음'으로, 측정하지 않은 값은 '측정 안 함'으로 적었다.

| ADR | 결정 |
|---|---|
| [0001](docs/adr/0001-vllm-engine.md) | 추론 엔진으로 vLLM |
| [0002](docs/adr/0002-constrained-decoding.md) | 제약 디코딩(xgrammar)과 완화 스키마(`allOf`를 `anyOf`로), 한계 U14 |
| [0003](docs/adr/0003-one-base-two-adapters.md) | 한 기반 모델에 LoRA-A·B 동시 적재, 릴리스 교체 |
| [0004](docs/adr/0004-release-bundle-and-hashes.md) | 릴리스 번들 형식과 해시 검증 |
| [0005](docs/adr/0005-serialize-engine-calls.md) | 엔진 호출 직렬화(락)와 큐 대신 503 |
| [0006](docs/adr/0006-request-deadline.md) | 요청 총 상한과 대기 상한(3.5초는 [0020](docs/adr/0020-per-call-deadline-budget.md)이 호출별 3.0/2.0초로 대체, 측정 안 함) |
| [0007](docs/adr/0007-retry-attempts.md) | 출력 재시도 3회 |
| [0008](docs/adr/0008-profiles.md) | 프로파일 gpu-12g(1.7B) / rtx4090-24g(8B) |
| [0009](docs/adr/0009-fake-backend-in-image.md) | fake 백엔드를 이미지에 두고 운영에서 차단 |
| [0010](docs/adr/0010-stdlib-http-server.md) | 표준 라이브러리 HTTP 서버 (0022가 대체) |
| [0011](docs/adr/0011-prompt-fixed-to-training-data.md) | 프롬프트 형식을 학습 데이터에 고정, 특수 토큰 처리 |
| [0012](docs/adr/0012-security-and-input-defaults.md) | 보안 기본값과 입력·연결 한도 |
| [0013](docs/adr/0013-gpu-session-kit.md) | GPU 세션 키트 |
| [0014](docs/adr/0014-pod-self-provisioning.md) | 임대 파드가 uv(고정)로 파이썬 3.12를 받고 gcc는 root면 apt로 스스로 준비 |
| [0015](docs/adr/0015-model-name-single-source.md) | 파드에서 받을 모델 이름은 프로파일의 base_model 하나에서만 읽고, `--model`이 다르면 시작 전 중단 |
| [0016](docs/adr/0016-pod-cost-guards.md) | 파드 비용 가드: 메모리 부족 기동 실패 시 util 자동 재시도, 한 기동 다단계 부하(--plan), 키트 커밋 기록 |
| [0017](docs/adr/0017-role-components-and-shared-retry.md) | LoRA-A·B를 두 컴포넌트로, 재시도는 공용 함수, 특수 토큰은 prompts.py |
| [0018](docs/adr/0018-mimic-components.md) | 데모용 모방 컴포넌트와 규칙표 데이터 파일 |
| [0019](docs/adr/0019-value-provenance.md) | 근거 없는 값(3.5초, 재시도 3, util 단계, 2500자, 릴리스 4개 등)의 출처 분류와 측정 계획. 값은 바꾸지 않는다 |
| [0020](docs/adr/0020-per-call-deadline-budget.md) | 호출별 상한 extract 3.0초·respond 2.0초(계약 표), 재시도는 남은 예산이 직전 시도 이상일 때만, 인증 실패 401 |
| [0021](docs/adr/0021-server-guarantees-action-rules.md) | LoRA-A 출력 규칙을 서버가 보증한다(계약 검사기 호출, `ExtractRequest`), turn-engine 시험 의존 제거 |
| [0022](docs/adr/0022-wsgi-waitress.md) | 추론 서버를 WSGI 앱 + waitress로(0010 대체), 연결·timeout·본문 상한을 waitress 설정으로 |
| [0023](docs/adr/0023-release-server-waitress.md) | GPU 세션 키트의 I4 읽기 서버도 WSGI 앱 + waitress로 |
| [0024](docs/adr/0024-server-applies-value-rules.md) | extract가 요청의 `fields`로 값 규칙(범위·R1~R3)을 적용한다 |
| [0025](docs/adr/0025-build-entrypoint-and-contract-schemas.md) | 영역 빌드 진입점 `scripts/build-image.sh`(계약 BuildImageRequest/Result)와 ServingConfig·RuntimeProfile·RuntimeFlowFile을 계약 스키마로 검증 |
| [0026](docs/adr/0026-setup-env-report.md) | `scripts/setup-env.sh`가 SetupEnvReport 한 줄로 시험 환경(.venv, test_command)을 선언한다 |
| [0027](docs/adr/0027-contract-3-transition.md) | 계약 3.0.0 전환: ExtractRequest만, action_rules_version, 라벨 정직성(U50) |
| [0028](docs/adr/0028-component-structure-d117.md) | 영역 구조: 컴포넌트·스키마·wiring, 통신 기반 라이브러리 위의 link_* (D117, U400) |

## 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| docs/brief.md | 영역의 목적, 경계, 수용 기준, GPU 호스트 규칙 |
| docs/boundary.md | 제공 API(I9), 소비 계약, 호출자가 지킬 것, 장애 영향 |
| docs/design.md | 현행 API 요약. v1 설계와 GPU 시험 기록(§11)은 docs/history/design-v1.md |
| docs/runbook.md | 환경 변수, 이미지, 컴포즈, LoRA 교체, 증상별 확인 |
| docs/STATUS.md | 차수별 한 일, 확인한 것과 못 한 것 |
| docs/rules.md | 출력 보호 규칙 |
| docs/prompt-review.md | 프롬프트 역할 분리 점검 목록 |
| docs/CHANGELOG.md | 변경 이력 |
| docs/change-requests/ | 헤드에게 보낸 변경 요청 001~004 |
