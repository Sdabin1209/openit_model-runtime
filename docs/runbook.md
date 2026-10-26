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

스키마를 이미지에 넣는 선택 옵션: `--build-context schema=<.../survey-service/schema>`를 더하면 `/opt/survey-schema`에 들어가고, `MODEL_RUNTIME_SCHEMA_DIR`가 없을 때 `/opt/survey-schema/schemas`를 쓴다(읽기 전용 마운트 불필요). 안 주면 빈 스테이지라 이미지에 스키마가 없고 마운트가 필요하다. 어느 쪽이든 정수 버전은 라벨 `io.openit.contracts.survey-schema`(`--build-arg SURVEY_SCHEMA_VERSION=<n>`)로 밝히고, 스키마 버전이 바뀌면 이미지를 다시 만든다(D21).

라벨 인자(선택): `--build-arg REVISION=<커밋> BOUNDARY_VERSION=<태그> SURVEY_SCHEMA_VERSION=<n> TURN_ENGINE_VERSION=<v>`(D50 라벨 `io.openit.*`, `io.openit.model-runtime`은 0.5.0). non-root(uid 10001), 포트 8080, 로그는 표준 출력, `HEALTHCHECK`는 `/healthz`. GPU 컨테이너는 `--gpus` 필요(런타임 `nvidia`).

### 환경 변수 (설정은 환경 변수로만 받는다)
| 변수 | 필수 | 기본 | 뜻 |
| --- | --- | --- | --- |
| `MODEL_RUNTIME_BACKEND` | 아니오 | `vllm` | `vllm` 또는 `fake` |
| `MODEL_RUNTIME_SCHEMA_DIR` | 이미지에 스키마를 넣지 않았으면 **예** | `/opt/survey-schema/schemas`(넣은 경우) | 문진 스키마의 `.../schema/schemas`(상위에 `CONTRACT_VERSION`). 이미지에 넣지 않으므로 **읽기 전용 마운트** |
| `MODEL_RUNTIME_RELEASE_URL` | **예** | | I4(release-gate) 서버의 **전체 주소**(예 `https://<호스트>:<포트>`). 컴포즈 서비스 이름이든 다른 호스트든 상관없다. 서버가 여기서 번들을 읽는다. 없거나 닿지 못하면 `/readyz` 503과 사유(`/healthz`는 200), 닿으면 `PUT` 적재가 된다 |
| `MODEL_RUNTIME_BASE_MODEL` | 아니오 | 프로파일의 `base_model` | 기반 모델 덮어쓰기(어댑터의 `base_model_name_or_path`와 같아야 함) |
| `HF_HOME`, `HF_HUB_OFFLINE` | 아니오 | `/var/lib/model-runtime/hf`, 꺼짐 | Hugging Face 표준 변수(vLLM이 읽는다): 모델 캐시 위치, 오프라인 모드 |
| `MODEL_RUNTIME_READY_PROBE_TTL_S` | 아니오 | `5` | `/readyz`의 I4 도달성 확인 캐시 시간 |
| `MODEL_RUNTIME_RELEASE_TOKEN` | I4가 인증을 요구하면 | 빈 값 | I4 읽기 토큰(비밀 값, 이미지·리포지토리에 넣지 않음) |
| `MODEL_RUNTIME_TOKENS` | 권장 | 빈 값(인증 없음) | `역할=토큰,역할=토큰`. 설정하면 `/v1/*`에 `Authorization: Bearer`가 필요하다(`/healthz`·`/readyz` 제외). survey-service의 `SURVEY_INFERENCE_TOKEN_ENV`가 가리키는 값과 같게 |
| `MODEL_RUNTIME_PROFILE` | vllm이면 **예** | | 프로파일 JSON 경로(`profiles/*.json`: `base_model`, `engine` 인자, `env`). 이미지에 `/app/profiles/rtx4090-24g.json`(잠정, 미시험)이 있다 |
| `MODEL_RUNTIME_BUNDLE_DIR` | 아니오 | `/var/lib/model-runtime/bundles` | 받은 번들을 푸는 곳. 이미지가 uid 10001 소유로 만들어 두므로 **새 이름 있는 볼륨을 처음 마운트해도 쓸 수 있다**(확인함). 바인드 마운트는 호스트에서 소유자를 10001로 맞춘다. 쓸 수 없으면 서버는 `/readyz` 503과 이유(`reason`)를 내고 `PUT`도 503 |
| `MODEL_RUNTIME_FAKE_RISK_PHRASE` | 아니오 | 꺼짐 | **fake 전용 시험 스위치**: 파서 입력 발화에 이 문구가 있으면 `RISK_SIGNAL` 항목 하나(`target`·`value` null, `source_text`는 발화)를 낸다. 위험 정지 → 직원 RESUME 시험용. vllm 백엔드에서 설정하면 서버가 시작을 거부하고, 켜져 있는지는 `/v1/info`의 `test_hooks.fake_risk_phrase`에 나온다 |
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
    MODEL_RUNTIME_RELEASE_URL: http://release-gate:8080   # 예: 같은 compose 네트워크의 서비스 이름. 다른 호스트면 아래 '단독 실행' 절
    MODEL_RUNTIME_PROFILE: /app/profiles/rtx4090-24g.json
  # MODEL_RUNTIME_TOKENS, MODEL_RUNTIME_RELEASE_TOKEN은 비밀 값으로 주입
  volumes: ["<문진 스키마>:/schema:ro", "bundles:/tmp/model-runtime-bundles"]
  deploy: {resources: {reservations: {devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]}}}
```
기반 모델 가중치는 이미지에 없다. 첫 적재 때 Hugging Face 캐시(`~/.cache/huggingface`, 컨테이너에서는 `HF_HOME`)로 받으므로 볼륨을 주고 네트워크 정책을 정한다(오프라인이면 미리 채운다).


### GPU로 실행하기 (표준 컨테이너)
**필요 조건**: NVIDIA 드라이버(호스트), Docker. 리눅스는 `nvidia-container-toolkit` 설치와 `docker run --gpus all`(또는 compose의 GPU 예약)이 되어야 한다. WSL2는 Docker Desktop의 WSL GPU 지원이 켜져 있어야 한다. 확인: `docker run --rm --gpus all <이미지> python -c "import vllm"`처럼 GPU 컨테이너가 뜨는지, 호스트에서 `nvidia-smi`가 보이는지.

**프로파일 선택**(같은 이미지, 환경 변수만 다르다): `MODEL_RUNTIME_PROFILE=/app/profiles/<파일>` — `gpu-12g.json`(12GB급 이하, 기반 모델 Qwen2.5-1.5B-Instruct, `gpu_memory_utilization` 0.8, `max_model_len` 8192, `max_num_seqs` 8; 근거는 파일의 `_comment`), `rtx4090-24g.json`(24GB, 잠정), `gpu-dev-12g.json`(실측한 보수적 값). 프로파일을 직접 만들어 마운트해도 된다(형식은 `load_profile`, `tests/test_neutral_package.py`가 형식을 검증). 기반 모델만 바꾸려면 `MODEL_RUNTIME_BASE_MODEL`.

**크기와 첫 기동**: GPU 이미지는 약 13GB이고 기반 모델 가중치(Qwen2.5-1.5B-Instruct)는 약 3GB(디스크 약 2.9GB)다. 가중치는 이미지에 없고 **첫 기동 때 Hugging Face에서 내려받아** `HF_HOME`(기본 `/var/lib/model-runtime/hf`, 이미지가 uid 10001 소유로 만들어 둠)에 캐시한다. HF 캐시는 **이름 있는 볼륨**으로 두면 재기동·재배포에도 다시 받지 않는다. 필요한 디스크는 이미지 13GB + 모델 3GB + 번들 캐시(어댑터 수십 MB)이고 네트워크는 첫 기동 때만 `huggingface.co`에 나가는 연결이 필요하다.

**오프라인이면**: 인터넷이 되는 곳에서 같은 볼륨(또는 같은 구조의 디렉터리)에 모델을 미리 받아 두고 옮긴다. 예: `docker run --rm -v mr-hf:/var/lib/model-runtime/hf -e HF_HOME=/var/lib/model-runtime/hf <이미지> python -c "from huggingface_hub import snapshot_download as d; d('Qwen/Qwen2.5-1.5B-Instruct')"` 뒤 볼륨을 옮기거나(`docker run --rm -v mr-hf:/v -v $PWD:/o busybox tar -C /v -cf /o/hf.tar .`) 오프라인 서버에서 풀고, 서버에는 `HF_HUB_OFFLINE=1`을 준다(없으면 기동 때 허브 접속을 시도해 실패할 수 있다).

**학습 없이 데모(중립 어댑터)**: `scripts/make_neutral_package.py OUT_DIR [--profile profiles/gpu-12g.json]`가 기반 모델 출력이 그대로 나오는 시험용 LoRA 패키지(LORA_A·LORA_B)를 결정적으로 만든다(표준 라이브러리만 필요, torch·vLLM 불필요; `model_runtime`이 import되는 환경에서). `OUT_DIR/<역할>/adapter/`(PEFT 형식, lora_B=0)와 `manifest.json`(ModelPackageManifest: backend vllm, 프로파일의 기반 모델, `ext."model-runtime".neutral`에 학습하지 않은 시험용 대체임을 명시)과 `SUMMARY.json`(해시)이 나온다. 같은 인자면 같은 해시. `--vllm-version`은 서빙 이미지의 vLLM(현재 0.31.0)과 같게. 모델 모르는 기반이면 `--model-config`로 HF `config.json`을 준다. 이 패키지로 릴리스를 만드는 일은 model-lab/release-gate의 몫이다. gpu-dev(vLLM 0.31.0, 12GB)에서 이 어댑터를 로딩해 온도 0 출력이 기반 모델과 같은 것을 확인했다(`scripts/gpu_neutral_check.py`).

### 다른 호스트에서 이 컨테이너만 단독으로 띄우기
이 서비스만 들어 있는 compose다. 서버는 **주소·토큰·프로파일을 환경 변수로만** 받으므로 deploy의 compose 네트워크나 서비스 이름에 의존하지 않는다(코드·이미지에 호스트 이름 기본값 없음; `tests/test_server.py::Location`이 확인).
```yaml
# compose.yaml (GPU 서버 한 대, 이 서비스만)
services:
  model-runtime:
    image: model-runtime:gpu            # 레지스트리 주소·digest는 환경에 맞게
    restart: unless-stopped
    ports: ["8080:8080"]                # 호출하는 쪽이 닿을 수 있는 주소로 게시
    environment:
      MODEL_RUNTIME_BACKEND: vllm
      MODEL_RUNTIME_PROFILE: /app/profiles/gpu-12g.json      # 24GB면 rtx4090-24g.json
      MODEL_RUNTIME_RELEASE_URL: https://release-gate.example.org:8443   # 다른 호스트의 release-gate(I4) 주소
      MODEL_RUNTIME_RELEASE_TOKEN: ${RELEASE_READ_TOKEN}     # I4 읽기 토큰(비밀 값, .env는 리포지토리에 두지 않는다)
      MODEL_RUNTIME_TOKENS: survey=${INFERENCE_CALLER_TOKEN} # 호출자(survey-service) 토큰
      MODEL_RUNTIME_SCHEMA_DIR: /schema/schemas               # 이미지에 스키마를 넣어 빌드했다면 생략
      # HF_HUB_OFFLINE: "1"                                   # 캐시를 미리 채운 오프라인 서버
    volumes:
      - hf-cache:/var/lib/model-runtime/hf
      - bundles:/var/lib/model-runtime/bundles
      - ./schema:/schema:ro                                   # 스키마를 이미지에 넣었다면 생략
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
volumes: {hf-cache: {}, bundles: {}}
```
호출하는 쪽(survey-service)은 `SURVEY_INFERENCE_URL`에 이 서버의 주소(`http://<이 호스트>:8080`)와 `SURVEY_INFERENCE_TOKEN_ENV`가 가리키는 호출자 토큰만 알면 된다. 모델 가중치·어댑터는 서버가 스스로 받는다.

**네트워크 조건(양방향 도달성)**

| 방향 | 무엇을 | 필요한 것 |
| --- | --- | --- |
| survey-service → model-runtime | `PUT`/`GET /v1/releases/{id}`, `POST .../parse`·`generate` | 게시한 포트(기본 8080)로의 TCP, 호출자 토큰 |
| model-runtime → release-gate(I4) | 번들 다운로드(`GET /v1/releases/{id}/bundle`)와 `GET /healthz` | `MODEL_RUNTIME_RELEASE_URL`의 주소·포트로의 TCP, 읽기 토큰. 컨테이너가 **그 주소를 해석·도달**할 수 있어야 한다(컴포즈 서비스 이름은 같은 네트워크에서만 풀린다) |
| model-runtime → huggingface.co | 기반 모델 첫 다운로드 | 443 외부 연결, 또는 캐시 볼륨을 미리 채우고 `HF_HUB_OFFLINE=1` |
| (운영) 모니터링 → model-runtime | `/healthz`, `/readyz` | 인증 없음. `/readyz`가 503이면 `reason`에 이유(번들 디렉터리 쓰기, I4 주소 미설정·도달 불가 등) |

주의: 서버의 I4 클라이언트는 환경 변수의 프록시 설정(`HTTP_PROXY`)을 읽지 않는다(boundary-contracts 구현). 프록시 뒤에 있으면 I4를 프록시 없이 직접 닿게 하거나 알려야 한다. 시작할 때 I4에 닿지 못해도 프로세스는 살아 있고 `/healthz`는 200, `/readyz`는 503과 사유이며, 닿는 즉시(확인 캐시 5초) 200이 되고 `PUT` 적재가 된다.

### fake 백엔드로 `PUT` 적재가 되려면
CPU 변형은 모델 없이 호출 경로·서비스 연동을 시험하는 용도다. 번들이 다음을 만족해야 한다.
0. **기대값 조회**: 서버가 `GET /v1/info`의 `serving_config_expectation`(`constraint`, 지원 프롬프트·보호 정책 버전, `output_contract`)으로 매니페스트가 맞춰야 할 값을 낸다(백엔드가 준비된 뒤). deploy는 이 값을 그대로 `serving_config`에 쓰면 된다.
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
