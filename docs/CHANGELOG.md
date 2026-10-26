# 변경 이력

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
