# 런북 (model-runtime)

라이브러리이므로 서비스처럼 직접 운영하지 않는다. 아래는 이 라이브러리를 쓰는 서비스·평가에서 증상이 나올 때의 확인 순서다.

## 개발 시험
`.venv`를 만들고 `pip install pytest jsonschema -e ../boundary-contracts -e .`를 한 뒤 `.venv/bin/python -m pytest`. GPU 불필요.

## GPU 시험 (gpu-dev, D47)
`scripts/gpu_check.sh [preflight|sync|setup|run|fetch|all]`. 호스트에 `python3-pip`·`python3-venv`가 있어야 한다(없으면 종료 코드 3, 설치하지 않는다). SSH가 두 번 실패하면 종료 코드 4(호스트 불가)이고 멈춘다. `sync`는 코드와 읽기 전용 입력의 사본(boundary-contracts, 문진 스키마)을 `~/openit-gpu/model-runtime/`로 보낸다. `setup`은 호스트 `.venv`에 `.[vllm]`을 설치하고(수 GB, GPU 안 씀) `runs/env-versions.txt`를 남긴다. `run`은 `flock -w 1800 ~/openit-gpu/.gpu.lock` 아래에서 `gpu_probe.py`를 서버 두 번(결정성)과 LoRA 끈 기준선 한 번 돌리고 `setsid nohup`으로 분리해 폴링한다. `fetch`는 `runs/<RUN_ID>/`(gitignore)로 가져와 `gpu_report.py`로 요약한다. 12GB 값은 `profiles/gpu-dev-12g.json`에 있다. 알려진 함정: nvcc가 없으면 `VLLM_USE_FLASHINFER_SAMPLER=0`(프로파일 `env`), 순위 초과 어댑터는 엔진을 죽인다. 결과 해석은 design.md §11-R.

## 서버와 컨테이너 (D62, 0.5.0) — deploy가 compose에 넣을 정보

### 이미지 두 변형
같은 `Dockerfile`, `VARIANT` 빌드 인자만 다르다. `boundary-contracts`는 형제 폴더를 읽기 전용 빌드 컨텍스트(`boundary`)로 받는다(CI에서는 태그 설치로 바꾼다). 의존은 `requirements-{cpu,gpu}.lock`으로 고정.

| 변형 | 빌드 | 용도 | 크기 |
| --- | --- | --- | --- |
| CPU/fake (기본) | `docker build --build-context boundary=../boundary-contracts -t model-runtime:cpu .` | `MODEL_RUNTIME_BACKEND=fake`. GPU·모델 없이 호출 경로·서비스 연동 시험 | 작음 |
| GPU | `docker build --build-arg VARIANT=gpu --build-context boundary=../boundary-contracts -t model-runtime:gpu .` | `MODEL_RUNTIME_BACKEND=vllm`(기본). vLLM 0.31.0, 서빙 목표 RTX 4090 24GB | 약 13GB |

라벨 인자(선택): `--build-arg REVISION=<커밋> BOUNDARY_VERSION=<태그> SURVEY_SCHEMA_VERSION=<n> TURN_ENGINE_VERSION=<v>`(D50 라벨 `io.openit.*`, `io.openit.model-runtime`은 0.5.0). non-root(uid 10001), 포트 8080, 로그는 표준 출력, `HEALTHCHECK`는 `/healthz`. GPU 컨테이너는 `--gpus` 필요(런타임 `nvidia`).

### 환경 변수 (설정은 환경 변수로만 받는다)
| 변수 | 필수 | 기본 | 뜻 |
| --- | --- | --- | --- |
| `MODEL_RUNTIME_BACKEND` | 아니오 | `vllm` | `vllm` 또는 `fake` |
| `MODEL_RUNTIME_SCHEMA_DIR` | **예** | | 문진 스키마의 `.../schema/schemas`(상위에 `CONTRACT_VERSION`). 이미지에 넣지 않으므로 **읽기 전용 마운트** |
| `MODEL_RUNTIME_RELEASE_URL` | **예** | | I4(release-gate) 서버 주소. 서버가 여기서 번들을 읽는다 |
| `MODEL_RUNTIME_RELEASE_TOKEN` | I4가 인증을 요구하면 | 빈 값 | I4 읽기 토큰(비밀 값, 이미지·리포지토리에 넣지 않음) |
| `MODEL_RUNTIME_TOKENS` | 권장 | 빈 값(인증 없음) | `역할=토큰,역할=토큰`. 설정하면 `/v1/*`에 `Authorization: Bearer`가 필요하다(`/healthz`·`/readyz` 제외). survey-service의 `SURVEY_INFERENCE_TOKEN_ENV`가 가리키는 값과 같게 |
| `MODEL_RUNTIME_PROFILE` | vllm이면 **예** | | 프로파일 JSON 경로(`profiles/*.json`: `base_model`, `engine` 인자, `env`). 이미지에 `/app/profiles/rtx4090-24g.json`(잠정, 미시험)이 있다 |
| `MODEL_RUNTIME_BUNDLE_DIR` | 아니오 | `/tmp/model-runtime-bundles` | 받은 번들을 푸는 곳(쓰기 가능, 볼륨 권장) |
| `MODEL_RUNTIME_QUEUE_WAIT_S` | 아니오 | `3.0` | 예상 대기가 이보다 길면 503. 서비스의 호출 타임아웃(기본 4초)보다 작게 |
| `MODEL_RUNTIME_PUT_WAIT_S` | 아니오 | `3.0` | `PUT` 응답이 적재 완료를 기다리는 시간. 넘으면 202 LOADING(이후 `GET`으로 확인) |
| `MODEL_RUNTIME_MAX_RELEASES` | 아니오 | `4` | 동시에 적재해 두는 릴리스 수 |
| `MODEL_RUNTIME_HOST` / `MODEL_RUNTIME_PORT` | 아니오 | `0.0.0.0` / `8080` | 바인드 |

서빙 설정(`serving_config`)은 환경 변수가 아니라 **릴리스 번들의 `manifest.adapters[역할].serving_config`와 그 해시**로 온다. 서버가 `load`에서 해시·백엔드(이름, 버전, 지원 기능)·순위·기반 모델을 검증한다. 백엔드는 `MODEL_RUNTIME_BACKEND`로 고르지만 매니페스트의 `constraint.backend`가 같아야 한다.

compose 조각(예시, 값은 deploy가 정한다):
```yaml
model-runtime:
  image: model-runtime:gpu
  environment:
    MODEL_RUNTIME_SCHEMA_DIR: /schema/schemas
    MODEL_RUNTIME_RELEASE_URL: http://release-gate:8080
    MODEL_RUNTIME_PROFILE: /app/profiles/rtx4090-24g.json
  # MODEL_RUNTIME_TOKENS, MODEL_RUNTIME_RELEASE_TOKEN은 비밀 값으로 주입
  volumes: ["<문진 스키마>:/schema:ro", "bundles:/tmp/model-runtime-bundles"]
  deploy: {resources: {reservations: {devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]}}}
```
기반 모델 가중치는 이미지에 없다. 첫 적재 때 Hugging Face 캐시(`~/.cache/huggingface`, 컨테이너에서는 `HF_HOME`)로 받으므로 볼륨을 주고 네트워크 정책을 정한다(오프라인이면 미리 채운다).

### fake 백엔드로 `PUT` 적재가 되려면
CPU 변형은 모델 없이 호출 경로·서비스 연동을 시험하는 용도다. 번들이 다음을 만족해야 한다.
1. `manifest.adapters.{LORA_A,LORA_B}.serving_config.constraint`가 `{"backend": "fake", "backend_version": "1", "supported_features": [FakeBackend 기본 집합], "relax_policy_version": "relax-1"}`. 집합은 `sorted(FakeBackend().supported_features)` = `$defs, $ref, additionalProperties, anyOf, enum, items, properties, required, type`. 만드는 가장 쉬운 방법은 `model_runtime.fake_serving_configs()`의 결과를 매니페스트에 넣는 것이다.
2. 나머지 `serving_config` 키는 평소와 같다(`prompt_template_version: prompt-2`, `output_contract: 1`, `protection_policy_version: protect-1`, `decoding`, `max_attempts`)이고 `serving_config_hash`는 `canonical.sha256_hash(serving_config)`.
3. `runtime_contract_version`은 1, `model_runtime_version`의 MAJOR.MINOR가 서버의 `RUNTIME_VERSION`과 같아야 한다(0.5.x).
4. 어댑터: 역할마다 **디렉터리가 존재하고** `adapter_hash`(`directory_hash`)가 맞으면 된다. fake는 내용을 읽지 않으므로 PEFT 형식(`adapter_config.json`의 `r`, `base_model_name_or_path`)은 요구하지 않는다. vllm 변형은 PEFT 형식과 `r <= max_lora_rank`, 기반 모델 일치가 필요하다(어긋나면 412).
5. fake의 출력은 고정이다(파서 `{"items": []}`, 생성기 `(가짜 응답)`)이므로 응답 내용으로 품질을 보지 않는다.
참고 구현: `tests/test_server_e2e.py`(참조 I4 서버 + 실제 `HttpReleaseReader`).

### 확인과 스모크
- `scripts/smoke.sh`(CPU/fake, 빈 포트 자동): `/healthz`, `/readyz`, `/v1/info`, 미적재 404, I4 없을 때 `PUT` 503, 라벨, non-root. 실제 모델 한 바퀴: `RUN_ID=<id> scripts/gpu_check.sh serve`(gpu-dev, flock).
- 적재: `PUT /v1/releases/{id}` → `GET`으로 상태(ABSENT/LOADING/LOADED/FAILED, 실패는 `error.code`). 412는 설정 해시·어댑터 순위/기반 모델/형식·PASS 아님, 422는 버전·무결성, 503은 I4 불가·엔진 실패. 재기동 뒤에는 서비스가 `PUT`을 다시 보낸다(미적재 릴리스는 404).
- `/readyz`가 503이면 서버 로그에서 백엔드 오류(`BackendNotAvailable` 등)를 본다. 엔진이 죽어 503이 계속되면 컨테이너를 다시 띄운다. 종료 시그널(SIGTERM)에는 엔진을 내린다.

## 증상별

| 증상(예외) | 뜻 | 확인 |
| --- | --- | --- |
| `ServingConfigMismatch` | 예외의 `role` 역할에 넘긴 `serving_config`의 해시가 그 역할 매니페스트의 `serving_config_hash`와 다르다 | 그 역할(`.role`) 매니페스트의 `serving_config`를 그대로 넘겼는지, 두 역할을 바꿔 넘기지 않았는지(키 순서는 무관, 값이 다르면 불일치). 설정을 고치지 말고 맞는 매니페스트를 쓴다. |
| `UnsupportedServingConfig` | 이 런타임이 모르는 템플릿 버전·`output_contract`·완화 정책·백엔드 | `RUNTIME_VERSION`과 매니페스트의 `lineage.model_runtime_version` 비교. 런타임을 올리거나 호환되는 릴리스를 쓴다. 릴리스 적재 중이면 이전 릴리스를 유지한다(I4). |
| `BackendNotAvailable` | 설정이 가리키는 백엔드가 이 환경에 없다 | GPU 이미지인지, `pip install .[vllm]`이 됐는지. vLLM 엔진이 `EngineDeadError`로 죽은 뒤에는 프로세스를 다시 띄워야 한다(0.4.0부터 순위 초과는 로딩 전에 `UnsupportedServingConfig`로 거부된다). |
| `ContractVersionMismatch` | `schema_dir` 상위의 `CONTRACT_VERSION`이 지원 값과 다르거나 읽을 수 없다 | `schema_dir`가 `.../schema/schemas`(계약 JSON이 있는 디렉터리)인지, 그 상위에 `CONTRACT_VERSION`이 있는지. |
| `ContractViolation` | 입력이 입력 계약을 위반했다(모델 호출 전). `serving_config` 형식 위반. 또는 `load`에 넘긴 어댑터·설정·기대 해시의 역할 집합이 {LORA_A, LORA_B}와 다르다 | 예외의 (경로, 키워드) 목록으로 어느 필드인지 본다. 값은 담기지 않는다. |
| (예외 아님) 출력 보호가 자주 적용된다 | 모델 출력이 깨져 규칙이 정한 대체 출력이 나갔다. 파서는 항목 고침·빈 목록, 생성기는 `is_fallback = true` | `last_call_info.outcome`·`protections`(P1~P6, G0·G1·G4, G5)를 집계한다. 어느 규칙이 많은지 보고, model-lab이 `parse_raw`/`generate_raw`로 보호 전 품질을 잰다. `relaxed` 목록이 크면 백엔드가 제약을 놓치는 것이므로 `supported_features`를 점검한다. 어댑터·템플릿 문제는 model-lab에 알린다. |

## 하지 말 것
- 발화나 모델 출력 원문을 로그에 남기려고 이 라이브러리의 예외·로그를 확장하지 않는다.
- 운영 중에 `serving_config`를 임의로 바꾸지 않는다. 바꾸려면 새 모델 패키지(새 해시)를 만든다.
