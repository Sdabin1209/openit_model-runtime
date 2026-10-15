# 변경 이력

## 0.4.0 (미배포) — 2026-10-15
- I9 시그니처 변경 없음. 실제 백엔드 추가와 어댑터 사전 검사(순위 r > `max_lora_rank`, 기반 모델 불일치는 `UnsupportedServingConfig`, PEFT 형식 아님은 `ContractViolation`)로 MINOR를 올렸다.
- 추가(D62): 추론 서버 `python -m model_runtime.server`, `Dockerfile`·`.dockerignore`·잠금 파일, `scripts/smoke.sh`, `scripts/gpu_serve_check.py`, 프로파일 `rtx4090-24g.json`(잠정), 변경 요청 003. 시험 88개.
- 서버 경로를 HTTP.md I9 절로 맞춤(릴리스 단위 PUT/GET/parse/generate, `X-Openit-*`, 큐 대기 503, SIGTERM 정리). `decoding.retry_temperature`(선택) 추가: 없으면 기존 동작·해시 불변. `VLLMBackend(None, ...)`로 엔진만 올리고 `bind(adapters)`로 릴리스별 어댑터를 묶음. 시험 100개.
- 제안(survey-service가 정할 일): 이미지 라벨에 vLLM·xgrammar 버전도 남긴다.
- `VLLMBackend`(vLLM 0.31.0 + 구조화 출력 + LoRA) 추가, `pip install .[vllm]`. 지연 import라 vLLM이 없는 환경에서도 패키지와 기존 시험이 그대로 돌고 `BackendNotAvailable`이다. `load(..., backend=None)`은 모든 역할의 `constraint.backend`가 `"vllm"`이면 `VLLMBackend(adapters)`를 만든다(기반 모델은 어댑터의 `adapter_config.json`에서 읽는다).
- `load_profile`, `DEFAULT_SUPPORTED_FEATURES`, 프로파일 `profiles/gpu-dev-12g.json`, GPU 시험 스크립트(`scripts/gpu_check.sh`, `gpu_probe.py`, `gpu_report.py`, `make_tiny_lora.py`), 단위 시험 14개(vLLM 모킹). 시험 81개.
- 변경 요청 002.

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
