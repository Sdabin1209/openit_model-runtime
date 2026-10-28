# 런북 (model-runtime)

라이브러리이므로 서비스처럼 직접 운영하지 않는다. 아래는 이 라이브러리를 쓰는 서비스·평가에서 증상이 나올 때의 확인 순서다.

## 개발 시험
`bash scripts/setup-env.sh`(정본: `.venv` 생성·재사용, pytest·jsonschema·`-e .`·boundary-contracts 태그 설치, 멱등, 형제 폴더 불필요)를 한 뒤 `.venv/bin/python -m pytest`. 계약 태그의 정본은 `Dockerfile`의 `ARG BOUNDARY_VERSION`이다. GPU 불필요. boundary-contracts는 형제 폴더 editable(`-e ../boundary-contracts`)이 아니라 **태그로 설치한다**(공통 규칙). `tests/test_server_e2e.py::test_reference_checkout_is_the_installed_tag`가 "설치된 패키지가 형제 작업 트리가 아니다"를 요구하고, 형제 `../boundary-contracts`는 읽기 전용 참조(적합성 시험·smoke의 git archive)로 같은 태그를 가리켜야 한다(`read_secret`이 v2.0.2부터 있다). 참조 I4 서버·conformance·예제는 설치된 태그 패키지의 `conformance`에서 읽어 형제 폴더 없이 돈다. 형제 폴더를 읽는 시험은 `tests/test_server_e2e.py::test_reference_checkout_is_the_installed_tag` 하나뿐이고, 형제 작업 트리가 없으면 건너뛴다. 설치본은 항상 태그 패키지다. 시험 상태(통과·건너뜀 수)는 README 5절이 정본이다. turn-engine·xgrammar가 없는 새 venv에서는 그 둘이 필요한 시험이 건너뛰어진다(`pytest -rs`의 SKIPPED 줄로 본다).

**turn-engine(선택, 시험 전용)**: `pyproject.toml`은 turn-engine 버전을 핀하지 않는다(버전 표를 여러 곳에 두지 않기 위해, U15). `tests/test_fake_flow.py`의 turn-engine 검증 2건은 `pytest.importorskip("turn_engine")`라 설치되어 있지 않으면 건너뛴다. 돌리려면 어떤 turn-engine인지 정해서 직접 설치한다. 기준은 deploy의 버전 표(`versions.env` 성격)가 정한 태그이고, 이 리포는 정하지 않는다. 예: `.venv/bin/pip install --no-deps "git+ssh://git@github.com/Sdabin1209/openit_turn-engine.git@<태그>"`. 건너뛴 시험 수는 `pytest -rs`의 SKIPPED 줄과 마지막 줄의 `skipped`로 본다.

## GPU 시험 (gpu-dev, D47)
`scripts/gpu_check.sh [preflight|sync|setup|run|fetch|all]`. 호스트에 `python3-pip`·`python3-venv`가 있어야 한다(없으면 종료 코드 3, 설치하지 않는다). SSH가 두 번 실패하면 종료 코드 4(호스트 불가)이고 멈춘다. `sync`는 코드와 읽기 전용 입력의 사본(boundary-contracts, 문진 스키마)을 `~/openit-gpu/model-runtime/`로 보낸다. `setup`은 호스트 `.venv`에 `.[vllm]`을 설치하고(수 GB, GPU 안 씀) `runs/env-versions.txt`를 남긴다. `run`은 `flock -w 1800 ~/openit-gpu/.gpu.lock` 아래에서 `gpu_probe.py`를 서버 두 번(결정성)과 LoRA 끈 기준선 한 번 돌리고 `setsid nohup`으로 분리해 폴링한다. `fetch`는 `runs/<RUN_ID>/`(gitignore)로 가져와 `gpu_report.py`로 요약한다. 12GB 값은 `profiles/gpu-dev-12g.json`에 있다. 알려진 함정: nvcc가 없으면 `VLLM_USE_FLASHINFER_SAMPLER=0`(프로파일 `env`), 순위 초과 어댑터는 엔진을 죽인다. 결과 해석은 design.md §11-R.

## 서버와 컨테이너 (D62, 0.5.0) — deploy가 compose에 넣을 정보

### 이미지 두 변형
같은 `Dockerfile`, `VARIANT` 빌드 인자만 다르다. `boundary-contracts`는 형제 폴더를 읽기 전용 빌드 컨텍스트(`boundary`)로 받는다(`scripts/smoke.sh`는 형제 폴더가 있으면 거기서 `git archive <태그>`로, 없으면 GitHub에서 태그를 clone해 만든 임시 폴더를 컨텍스트로 넘긴다. 직접 빌드할 때는 그런 태그 소스 폴더를 `--build-context boundary=<폴더>`로 준다). 의존은 `requirements-{cpu,gpu}.lock`으로 고정.

| 변형 | 빌드 | 용도 | 크기 |
| --- | --- | --- | --- |
| CPU/fake (기본) | `docker build --build-context boundary=../boundary-contracts -t model-runtime:cpu .` | `MODEL_RUNTIME_BACKEND=fake` + `MODEL_RUNTIME_ALLOW_FAKE=1`(개발·시험 전용). GPU·모델 없이 호출 경로·서비스 연동 시험 | 작음 |
| GPU | `docker build --build-arg VARIANT=gpu --build-context boundary=../boundary-contracts -t model-runtime:gpu .` | `MODEL_RUNTIME_BACKEND=vllm`(기본). vLLM 0.31.0, 서빙 목표 RTX 4090 24GB | 약 13GB |

스키마를 이미지에 넣는 선택 옵션: `--build-context schema=<.../survey-service/schema>`를 더하면 `/opt/survey-schema`에 들어가고, `MODEL_RUNTIME_SCHEMA_DIR`가 없을 때 `/opt/survey-schema/schemas`를 쓴다(읽기 전용 마운트 불필요). 안 주면 빈 스테이지라 이미지에 스키마가 없고 마운트가 필요하다. 어느 쪽이든 정수 버전은 라벨 `io.openit.contracts.survey-schema`(`--build-arg SURVEY_SCHEMA_VERSION=<n>`)로 밝히고, 스키마 버전이 바뀌면 이미지를 다시 만든다(D21).

라벨 인자(선택): `--build-arg REVISION=<커밋> BOUNDARY_VERSION=<태그> SURVEY_SCHEMA_VERSION=<n> TURN_ENGINE_VERSION=<v>`(D50 라벨 `io.openit.*`, `io.openit.model-runtime`은 빌드 인자 `MODEL_RUNTIME_VERSION`, 정본은 pyproject의 version). non-root(uid 10001), 포트 8080, 로그는 표준 출력, `HEALTHCHECK`는 `/healthz`. GPU 컨테이너는 `--gpus` 필요(런타임 `nvidia`).

### 환경 변수 (설정은 환경 변수로만 받는다)
| 변수 | 필수 | 기본 | 뜻 |
| --- | --- | --- | --- |
| `MODEL_RUNTIME_BACKEND` | 아니오 | `vllm` | `vllm`, `fake`, `mimic`(데모용 모방 컴포넌트, ADR 0018) |
| `MODEL_RUNTIME_SCHEMA_DIR` | 이미지에 스키마를 넣지 않았으면 **예** | `/opt/survey-schema/schemas`(넣은 경우) | 문진 스키마의 `.../schema/schemas`(상위에 `CONTRACT_VERSION`). 이미지에 넣지 않으므로 **읽기 전용 마운트** |
| `MODEL_RUNTIME_RELEASE_URL` | **예** | | I4(release-gate) 서버의 **전체 주소**(예 `https://<호스트>:<포트>`). 컴포즈 서비스 이름이든 다른 호스트든 상관없다. 서버가 여기서 번들을 읽는다. 없거나 닿지 못하면 `/readyz` 503과 사유(`/healthz`는 200), 닿으면 `PUT` 적재가 된다 |
| `MODEL_RUNTIME_BASE_MODEL` | 아니오 | 프로파일의 `base_model` | 기반 모델 덮어쓰기(어댑터의 `base_model_name_or_path`와 같아야 함) |
| `HF_HOME`, `HF_HUB_OFFLINE` | 아니오 | `/var/lib/model-runtime/hf`, 꺼짐 | Hugging Face 표준 변수(vLLM이 읽는다): 모델 캐시 위치, 오프라인 모드 |
| `MODEL_RUNTIME_READY_PROBE_TTL_S` | 아니오 | `5` | `/readyz`의 I4 도달성 확인 캐시 시간 |
| `MODEL_RUNTIME_RELEASE_TOKEN_FILE` / `MODEL_RUNTIME_RELEASE_TOKEN` | I4가 인증을 요구하면 | 빈 값 | I4 읽기 토큰(비밀 값, D84). **파일 경로(`_FILE`)가 우선**이고 환경 변수는 개발·시험용이다(`boundary_contracts.read_secret`). 값은 로그·응답·오류에 나오지 않는다. compose `secrets:`의 `/run/secrets/...`를 가리킨다 |
| `MODEL_RUNTIME_TOKENS_FILE` / `MODEL_RUNTIME_TOKENS` | **필수** | 없음(토큰이 없으면 시작 거부, 개발용으로만 `MODEL_RUNTIME_ALLOW_NO_AUTH=1`) | `역할=토큰,역할=토큰`. 설정하면 `/v1/*`에 `Authorization: Bearer`가 필요하다(`/healthz`·`/readyz` 제외). survey-service의 `SURVEY_INFERENCE_TOKEN_ENV`가 가리키는 값과 같게. 비밀이므로 `_FILE`(D84)로 준다. 시작 로그에 값이 아니라 출처(`file`/`env`/`unset`)만 한 줄 남는다. `/readyz`·`/v1/info`에는 출처도 내지 않는다(HTTP 모양을 바꾸지 않기 위해) |
| `MODEL_RUNTIME_PROFILE` | vllm이면 **예** | | 프로파일 JSON 경로(`profiles/*.json`: `base_model`, `engine` 인자, `env`). 이미지에 `/app/profiles/rtx4090-24g.json`(Qwen3-8B, 적재 가능 여부 미확인)이 있다 |
| `MODEL_RUNTIME_BUNDLE_DIR` | 아니오 | `/var/lib/model-runtime/bundles` | 받은 번들을 푸는 곳. 이미지가 uid 10001 소유로 만들어 두므로 **새 이름 있는 볼륨을 처음 마운트해도 쓸 수 있다**(확인함). 바인드 마운트는 호스트에서 소유자를 10001로 맞춘다. 쓸 수 없으면 서버는 `/readyz` 503과 이유(`reason`)를 내고 `PUT`도 503 |
| `MODEL_RUNTIME_FAKE_RISK_PHRASE` | 아니오 | (없어짐) | **2.0.0에서 없어졌다.** v2 출력에는 위험 신호 표현이 없다(survey-service와 헤드의 미정 항목). 설정이 남아 있으면 서버가 시작을 거부한다 |
| `MODEL_RUNTIME_FAKE_FLOW_FILE` | 아니오 (fake 전용) | 없음 | 문항 ID 목록 JSON(`["Q1.D01", ...]` 또는 `{"order": [...]}`, survey-service의 문항 순서와 같게). 객체 형식이면 선택 항목 `slots`(`{"Q4": [{"key": "Q4.answer", "type": "boolean"}]}`, type은 boolean 또는 integer, 첫 칸만 쓴다)와 `skips`(survey-service QuestionFlow와 같은 `[{"slot","op":"==","value","skip_item"}]`)를 줄 수 있고, 그러면 칸이 정의된 문항에서 환자 발화로 ANSWERED 값을 낸다(0.7.1: boolean은 발화에 '아니'가 있으면 false 아니면 true, integer는 첫 숫자열, 없고 '아니'·'없'이면 0, 그 밖은 빈 values). 값에 skips를 적용한 next_item을 낸다. 선택 항목 `corrections`(0.7.2)로 정정 응답도 낸다: `{"order":[...,"Q4","Q4_1","Q5",...],"corrections":[{"contains":"아까 말","key":"Q4.answer","value":false,"at":"Q5"}]}` 이면 Q5를 묻는 중 발화에 "아까 말"이 있을 때 `{"action":"CORRECT","next_item":"Q5","values":[{"key":"Q4.answer","precision":"exact","save":true,"status":"CORRECTION","value":false}]}`를 낸다(`at`을 생략하면 어느 문항에서나, next_item은 정정한 문항 다음에 skips를 적용한 문항, `slots`보다 먼저 적용). 선택 항목 `reasks`(0.7.5)는 되묻기 응답을 낸다(서비스의 REASK_LIMIT 시험용): `[{"contains":"글쎄요","key":"Q4.answer","reason":"MISSING"(선택),"at":"Q4"(선택)}]` 이면 발화에 문구가 있을 때마다 `{"action":"REASK","next_item":<입력 question_id>,"reason":...,"targets":[key],"values":[그 칸 MISSING]}`를 낸다(corrections보다 먼저 적용). 있으면 LORA-A가 입력 `question_id`의 다음 문항으로 가는 `NEXT`(values 비어 있음, 마지막이면 `END`)를 낸다. 파일이 없거나 형식이 틀리면 서버가 시작을 거부한다 |
| `MODEL_RUNTIME_FAKE_EXTRACTION` | 아니오 (fake 전용) | 없음 | LORA-A가 항상 내는 고정 출력(JSON 문자열). `FLOW_FILE`보다 우선. 스키마·의미 검증은 하지 않으므로 쓰는 쪽 책임 |
| **주의** | | | **아래 `EXTRACT_DEADLINE_S`(3.0)·`RESPOND_DEADLINE_S`(2.0)·`QUEUE_WAIT_S`(3.0)·`BACKEND_RETRY_S`는 실제 모델 지연으로 측정한 적이 없는 초기값이다. 가짜 백엔드·1.7B 측정(중앙값 약 1.7초)에서 나왔고, 8B(24GB GPU)에서는 호출 하나가 상한(extract 3.0초, respond 2.0초)을 넘어 `ENGINE_TIMEOUT`이 날 수 있다. H1(24GB 실측)에서 다시 정해야 한다.** 절차는 docs/boundary.md "실측 절차" |
| `MODEL_RUNTIME_ALLOW_NO_AUTH` | 아니오 | 없음 | `1`이면 토큰 없이 시작(개발용). 운영에서 쓰지 않는다 (ADR 0012) |
| `MODEL_RUNTIME_ALLOW_FAKE` | fake·mimic일 때 필수 | 없음 | `MODEL_RUNTIME_BACKEND=fake` 또는 `mimic`은 이 값이 `1`이 아니면 시작 거부 (ADR 0009, 0018) |
| `MODEL_RUNTIME_MIMIC_FLOW_FILE` | 아니오 (mimic 전용) | `MODEL_RUNTIME_FAKE_FLOW_FILE` | 문항 흐름 파일. fake와 같은 형식(`order`, `slots`, `skips`, `corrections`, `reasks`). 칸 정의(`slots`)가 없는 문항에서는 값을 내지 않는다 |
| `MODEL_RUNTIME_MIMIC_RULES_FILE` | 아니오 (mimic 전용) | 패키지의 `data/mimic_rules.json` | 모방 규칙표(낱말 목록, 응답 문장 틀). 형태가 틀리면 시작 거부 |
| `MODEL_RUNTIME_PUT_ROLES` | 아니오 | 없음(인증된 모든 역할) | 적재 PUT을 허용할 역할 목록(쉼표). 토큰 역할 `loader`는 PUT만 가능하고 extract/respond는 못 한다 |
| `MODEL_RUNTIME_MAX_INPUT_CHARS` | 아니오 | `2500` | 입력(JSON 직렬화)의 문자 수 상한, 초과 400. 추정값(토큰 수 측정 안 함), ADR 0012 |
| `MODEL_RUNTIME_MAX_CONNECTIONS` / `MAX_LOADING` / `CONN_TIMEOUT_S` / `SHUTDOWN_GRACE_S` | 아니오 | `32` / `2` / `15` / `5` | 동시 연결, 동시 적재, 연결 소켓 timeout(초), 종료 시 진행 요청 대기(초). 초기값 (ADR 0012) |
| `MODEL_RUNTIME_HOST` | 아니오 | `127.0.0.1` | 이미지는 `0.0.0.0`으로 연다(Dockerfile). compose는 포트를 `127.0.0.1:8080:8080`처럼 게시한다 |
| `MODEL_RUNTIME_EXTRACT_DEADLINE_S` / `MODEL_RUNTIME_RESPOND_DEADLINE_S` | 아니오 | `3.0` / `2.0` | 호출 하나의 총 상한(대기+추론+재시도), 호출 종류별. 넘기면 503 `ENGINE_TIMEOUT`. 계약 CONVENTIONS '시간 상한 사슬'(2.2.0) 표 값: 한 턴이 extract→respond 두 번이라 합 5.0초 + 여유 1.0초 = 턴 마감 6초. 재시도는 남은 예산이 직전 시도 시간 이상일 때만 한다. **측정하지 않은 배분값**(ADR 0020). 옛 `MODEL_RUNTIME_REQUEST_DEADLINE_S`(3.5초 하나)는 없어졌고 설정되어 있으면 시작을 거부한다 |
| `MODEL_RUNTIME_BACKEND_RETRY_S` | 아니오 | `30` | 백엔드 기동(모델 내려받기 등) 실패 후 재시도 간격. 0이면 재시도 없음 |
| `MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S` | 아니오 | `120` | I4 번들 읽기 소켓 timeout |
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
    MODEL_RUNTIME_TOKENS_FILE: /run/secrets/model_runtime_tokens   # 비밀은 파일로(D84)
    MODEL_RUNTIME_RELEASE_TOKEN_FILE: /run/secrets/model_runtime_release_token
  secrets: [model_runtime_tokens, model_runtime_release_token]
  volumes: ["<문진 스키마>:/schema:ro", "bundles:/tmp/model-runtime-bundles"]
  deploy: {resources: {reservations: {devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]}}}
```
기반 모델 가중치는 이미지에 없다. 첫 적재 때 Hugging Face 캐시(`~/.cache/huggingface`, 컨테이너에서는 `HF_HOME`)로 받으므로 볼륨을 주고 네트워크 정책을 정한다(오프라인이면 미리 채운다).


### GPU로 실행하기 (표준 컨테이너)
**필요 조건**: NVIDIA 드라이버(호스트), Docker. 리눅스는 `nvidia-container-toolkit` 설치와 `docker run --gpus all`(또는 compose의 GPU 예약)이 되어야 한다. WSL2는 Docker Desktop의 WSL GPU 지원이 켜져 있어야 한다. 확인: `docker run --rm --gpus all <이미지> python -c "import vllm"`처럼 GPU 컨테이너가 뜨는지, 호스트에서 `nvidia-smi`가 보이는지.

**프로파일 선택**(같은 이미지, 환경 변수만 다르다): `MODEL_RUNTIME_PROFILE=/app/profiles/<파일>` — `gpu-12g.json`(12GB급, 기반 모델 **Qwen3-1.7B**, **파이프라인 데모용**(D81): 학습된 Qwen3-4B/8B 어댑터는 이 프로파일에 올리지 않고 기반 모델 불일치로 412 거부된다. `gpu_memory_utilization` 0.8, `max_model_len` 4096, `max_num_seqs` 4; 이전 Qwen2.5-1.5B 설정(0.8/8192/8, 약 10.2GB 사용, 적재 성공)에서 출발한 보수적 추정이며 **Qwen3-1.7B가 12GB에서 적재되는지, A·B 어댑터를 동시에 올릴 수 있는지는 미확인**), `rtx4090-24g.json`(24GB급, **Qwen3-8B**, 학습된 어댑터의 서빙·평가 대상, 마찬가지로 미확인), `gpu-dev-12g.json`(Qwen2.5-1.5B로 실측한 보수적 값, 중립 어댑터·파이프라인 시험 전용). 어댑터의 기반 모델이 프로파일과 다르면 적재를 거부한다(412). 근거와 계산은 각 파일의 `_comment`·`status`. 프로파일을 직접 만들어 마운트해도 된다(형식은 `load_profile`, `tests/test_neutral_package.py`가 형식을 검증). 기반 모델만 바꾸려면 `MODEL_RUNTIME_BASE_MODEL`.

**HF 캐시 마운트 경로 주의**: 볼륨은 이미지가 이미 만들고 uid 10001 소유로 해 둔 `/var/lib/model-runtime/hf`(기본 `HF_HOME`)에 마운트한다. 다른 경로(`HF_HOME=/var/lib/hf-cache` 등)를 쓰려면 Dockerfile에서 그 경로를 `mkdir` 하고 `chown 10001` 해야 한다. 아니면 마운트 지점이 root 소유라 서버가 모델을 받지 못하고, 서버는 시작 때 검사해 `/readyz` 503 reason을 `hf cache not writable: <경로> (uid <n>)`로 낸다(오프라인 `HF_HUB_OFFLINE=1`이나 fake 백엔드에서는 검사하지 않는다).

**이미지에 들어 있는 것(0.5.3)**: GPU 변형은 C 컴파일러(`gcc`, `libc6-dev`, `CC=gcc`)를 포함한다. vLLM이 LoRA 커널을 쓸 때 Triton이 CUDA 유틸 모듈을 C 소스에서 빌드하는데(`enforce_eager`로 피해지지 않는다고 판단, 확정은 아님) 컴파일러가 없으면 `RuntimeError: Failed to find C compiler`로 적재가 실패한다. 컴파일러는 공격면을 늘리지만 vLLM 공식 이미지도 포함한다. Python 헤더는 python 베이스 이미지에 이미 있어 따로 넣지 않는다(CPU 변형에는 컴파일러가 없다). **미확인**: Triton이 nvcc 없이 쓰는 CUDA 헤더(Triton이 자체 `cuda.h`를 동봉한다고 알려져 있음)와 `libcuda`(NVIDIA 컨테이너 런타임이 주입하는 `libcuda.so.1`) 링크가 이 이미지에서 실제로 되는지는 GPU 컨테이너 실행으로 확인해야 한다. FlashInfer JIT 샘플러는 nvcc가 필요해 프로파일 `env`의 `VLLM_USE_FLASHINFER_SAMPLER=0`으로 끈다(세 프로파일 모두 들어 있음). 이미지 ENV 기본값은 `HOME=/var/lib/model-runtime/home`, `XDG_CACHE_HOME`·`VLLM_CACHE_ROOT`·`TORCHINDUCTOR_CACHE_DIR`·`TRITON_CACHE_DIR`·`TORCH_HOME`(`/var/lib/model-runtime/cache/...`), 원격 측정 차단(`VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1`, `HF_HUB_DISABLE_TELEMETRY=1`)이며 compose가 같은 변수로 덮어써도 된다(읽기 전용 루트에서도 쓸 수 있는 경로를 줄 것). `/var/lib/model-runtime`에 볼륨을 마운트하면 이미지의 하위 디렉터리가 가려지므로 서버가 시작할 때 이 경로들을 `mkdir -p`로 보장하고, 쓸 수 없으면 `/readyz` 503 reason `runtime dir not writable: <경로> (uid <n>)`을 낸다.

**크기와 첫 기동**: GPU 이미지는 약 13GB이고 기반 모델 가중치(Qwen2.5-1.5B-Instruct)는 약 3GB(디스크 약 2.9GB)다. 가중치는 이미지에 없고 **첫 기동 때 Hugging Face에서 내려받아** `HF_HOME`(기본 `/var/lib/model-runtime/hf`, 이미지가 uid 10001 소유로 만들어 둠)에 캐시한다. HF 캐시는 **이름 있는 볼륨**으로 두면 재기동·재배포에도 다시 받지 않는다. 필요한 디스크는 이미지 13GB + 모델 3GB + 번들 캐시(어댑터 수십 MB)이고 네트워크는 첫 기동 때만 `huggingface.co`에 나가는 연결이 필요하다.

**오프라인이면**: 인터넷이 되는 곳에서 같은 볼륨(또는 같은 구조의 디렉터리)에 모델을 미리 받아 두고 옮긴다. 예: `docker run --rm -v mr-hf:/var/lib/model-runtime/hf -e HF_HOME=/var/lib/model-runtime/hf <이미지> python -c "from huggingface_hub import snapshot_download as d; d('Qwen/Qwen2.5-1.5B-Instruct')"` 뒤 볼륨을 옮기거나(`docker run --rm -v mr-hf:/v -v $PWD:/o busybox tar -C /v -cf /o/hf.tar .`) 오프라인 서버에서 풀고, 서버에는 `HF_HUB_OFFLINE=1`을 준다(없으면 기동 때 허브 접속을 시도해 실패할 수 있다).

**학습 없이 데모(중립 어댑터)**: `scripts/make_neutral_package.py OUT_DIR [--profile profiles/gpu-12g.json]`가 기반 모델 출력이 그대로 나오는 시험용 LoRA 패키지(LORA_A·LORA_B)를 결정적으로 만든다(표준 라이브러리만 필요, torch·vLLM 불필요; `model_runtime`이 import되는 환경에서). `OUT_DIR/<역할>/adapter/`(PEFT 형식, lora_B=0)와 `manifest.json`(ModelPackageManifest: backend vllm, 프로파일의 기반 모델, `ext."model-runtime".neutral`에 학습하지 않은 시험용 대체임을 명시)과 `SUMMARY.json`(해시)이 나온다. 같은 인자면 같은 해시. `--vllm-version`은 서빙 이미지의 vLLM(현재 0.31.0)과 같게. 모르는 기반(Qwen3 포함: 확인하지 못한 수치를 내장하지 않는다)이면 `--model-config`로 HF `config.json`(`head_dim` 포함)을 준다. **기본 프로파일 `gpu-12g`(Qwen3-1.7B)의 차원은 Hugging Face `Qwen/Qwen3-1.7B`의 `config.json`(`hidden_size`, `num_hidden_layers`, `num_attention_heads`, `num_key_value_heads`, `head_dim`)에서 얻는다.** 이 리포지토리는 그 수치를 담지 않고 이 PC에서 확인하지도 못했다. 리비전(커밋)을 고정해서 받고 파일의 sha256을 검사하는 일은 deploy의 몫이다(제안; 미정). 패키지에는 시험용 자리표시 `system_prompt.txt`가 들어간다(학습과 짝인 프롬프트가 아니다). 이 패키지로 릴리스를 만드는 일은 model-lab/release-gate의 몫이다. gpu-dev(vLLM 0.31.0, 12GB)에서 이 어댑터를 로딩해 온도 0 출력이 기반 모델과 같은 것을 확인했다(`scripts/gpu_neutral_check.py`).

### 다른 호스트에서 이 컨테이너만 단독으로 띄우기
이 서비스만 들어 있는 compose다. 서버는 **주소·토큰·프로파일을 환경 변수로만** 받으므로 deploy의 compose 네트워크나 서비스 이름에 의존하지 않는다(코드·이미지에 호스트 이름 기본값 없음; `tests/test_server.py::Location`이 확인).
```yaml
# compose.yaml (GPU 서버 한 대, 이 서비스만)
services:
  model-runtime:
    image: model-runtime:gpu            # 레지스트리 주소·digest는 환경에 맞게
    restart: unless-stopped
    ports: ["127.0.0.1:8080:8080"]      # 필요한 주소로만 게시한다(전체 게시 금지, 인증은 토큰 필수)
    environment:
      MODEL_RUNTIME_BACKEND: vllm
      MODEL_RUNTIME_PROFILE: /app/profiles/gpu-12g.json      # 24GB급이면 rtx4090-24g.json
      MODEL_RUNTIME_RELEASE_URL: https://release-gate.example.org:8443   # 다른 호스트의 release-gate(I4) 주소
      MODEL_RUNTIME_RELEASE_TOKEN: ${RELEASE_READ_TOKEN}     # I4 읽기 토큰(비밀 값, .env는 리포지토리에 두지 않는다)
      MODEL_RUNTIME_TOKENS_FILE: /run/secrets/model_runtime_tokens # 비밀은 파일로(D84). 개발용 환경 변수 MODEL_RUNTIME_TOKENS는 호환일 뿐
      MODEL_RUNTIME_SCHEMA_DIR: /schema/schemas               # 이미지에 스키마를 넣어 빌드했다면 생략
      # HF_HUB_OFFLINE: "1"                                   # 캐시를 미리 채운 오프라인 서버
    volumes:
      - hf-cache:/var/lib/model-runtime/hf   # 이미지가 uid 10001 소유로 준비한 경로. HF_HOME을 덮어쓰지 않는다
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
| survey-service → model-runtime | `PUT`/`GET /v1/releases/{id}`, `POST .../extract`·`respond` | 게시한 포트(기본 8080)로의 TCP, 호출자 토큰 |
| model-runtime → release-gate(I4) | 번들 다운로드(`GET /v1/releases/{id}/bundle`)와 `GET /healthz` | `MODEL_RUNTIME_RELEASE_URL`의 주소·포트로의 TCP, 읽기 토큰. 컨테이너가 **그 주소를 해석·도달**할 수 있어야 한다(컴포즈 서비스 이름은 같은 네트워크에서만 풀린다) |
| model-runtime → huggingface.co | 기반 모델 첫 다운로드 | 443 외부 연결, 또는 캐시 볼륨을 미리 채우고 `HF_HUB_OFFLINE=1` |
| (운영) 모니터링 → model-runtime | `/healthz`, `/readyz` | 인증 없음. `/readyz`가 503이면 `reason`에 이유(번들 디렉터리 쓰기, I4 주소 미설정·도달 불가 등) |

주의: 서버의 I4 클라이언트는 환경 변수의 프록시 설정(`HTTP_PROXY`)을 읽지 않는다(boundary-contracts 구현). 프록시 뒤에 있으면 I4를 프록시 없이 직접 닿게 하거나 알려야 한다. 시작할 때 I4에 닿지 못해도 프로세스는 살아 있고 `/healthz`는 200, `/readyz`는 503과 사유이며, 닿는 즉시(확인 캐시 5초) 200이 되고 `PUT` 적재가 된다.

### 학습 데이터와 프롬프트 글자 일치 점검 (D77)
학습 서버에서 읽기 전용으로 표준출력에 흘려 이 PC에서 점검한다(파일을 저장하지 않는다). 서버에는 아무것도 쓰거나 설치하지 않는다.
- `ssh openit 'head -n 60 ~/taehyeon/data/dataset_10_14/lora_a_val.jsonl' | .venv/bin/python scripts/check_prompt_matches_dataset.py` (B도 같다. `--shuffle`은 입력 키 순서를 섞어도 같은지 본다). 종료 코드 0이면 모두 일치.
- 채팅 형식: `python scripts/check_chat_template.py <Qwen3 tokenizer_config.json>`(jinja2 필요). 토크나이저 토큰 ID 대조는 아니다.
- 프롬프트 템플릿을 고치면 이 점검을 다시 하고 새 버전을 만든다(기존 버전의 출력은 골든 시험이 고정한다).

### fake 백엔드로 `PUT` 적재가 되려면
CPU 변형은 모델 없이 호출 경로·서비스 연동을 시험하는 용도다. 번들이 다음을 만족해야 한다.
0. **기대값 조회**: 서버가 `GET /v1/info`의 `serving_config_expectation`(`constraint`, 지원 프롬프트·보호 정책 버전, `output_contract`)으로 매니페스트가 맞춰야 할 값을 낸다(백엔드가 준비된 뒤). deploy는 이 값을 그대로 `serving_config`에 쓰면 된다.
1. `manifest.adapters.{LORA_A,LORA_B}.serving_config.constraint`가 `{"backend": "fake", "backend_version": "1", "supported_features": [FakeBackend 기본 집합], "relax_policy_version": "relax-3"}`. 집합은 `sorted(FakeBackend().supported_features)` = `$defs, $ref, additionalProperties, anyOf, enum, items, properties, required, type`. 만드는 가장 쉬운 방법은 `model_runtime.fake_serving_configs()`의 결과를 매니페스트에 넣는 것이다.
2. 나머지 `serving_config` 키는 평소와 같다(`prompt_template_version: prompt-4`(유일한 값; prompt-3은 0.7.0에서 제거되어 거부된다. `/v1/info`의 `prompt_template_versions` 참고), `output_contract: 1`, `protection_policy_version: protect-2`, `decoding`(LORA_B는 `constraint_mode: "none"` 권장), `max_attempts`)이고 `serving_config_hash`는 `canonical.sha256_hash(serving_config)`.
3. `runtime_contract_version`은 1, `model_runtime_version`의 MAJOR.MINOR가 서버의 `RUNTIME_VERSION`과 같아야 한다(0.8.x). 0.7.x로 만든 릴리스는 0.8.0 서버에서 `UNSUPPORTED_VERSION`으로 거부된다.
4. 어댑터: 역할마다 **디렉터리가 존재하고** `adapter_hash`(`directory_hash`)가 맞고, **시스템 프롬프트 파일**(`adapters[role].system_prompt`의 `path`, `sha256`; 어댑터 디렉터리 안, 비어 있지 않은 UTF-8)이 있어야 한다(D75). fake는 내용을 읽지 않으므로 PEFT 형식(`adapter_config.json`의 `r`, `base_model_name_or_path`)은 요구하지 않는다. vllm 변형은 PEFT 형식과 `r <= max_lora_rank`, 기반 모델 일치가 필요하다(어긋나면 412).
5. fake의 출력은 입력과 무관하게 결정적이다(extract: 설정이 없으면 `{"action":"NEXT","next_item":"END","values":[]}` — 첫 문항에서도 END라 survey-service의 `validate_extraction`이 반려한다. 문항 순서를 `MODEL_RUNTIME_FAKE_FLOW_FILE`로 주면 입력 문항의 다음 문항을 낸다. respond `(가짜 응답)`)이므로 응답 내용으로 품질을 보지 않는다.
참고 구현: `tests/test_server_e2e.py`(참조 I4 서버 + 실제 `HttpReleaseReader`).

### LoRA 교체 (학습된 어댑터를 얹는 절차, H2)
어댑터가 바뀌어도 이 서비스의 코드·이미지는 바뀌지 않는다. 바뀌는 것은 **릴리스**(번들)다. 단계마다 담당 영역이 다르다. 이 리포가 직접 확인한 것은 1단계의 형식과 4~5단계의 서빙 쪽이고, 2~3단계의 명령은 model-lab·release-gate가 정한다(이 리포는 그 명령을 알지 못하므로 적지 않는다. **미확인**).
1. **어댑터 디렉터리 준비**(model-lab, 학습 결과): 역할마다 PEFT 형식(`adapter_config.json`의 `r`, `base_model_name_or_path`, `adapter_model.safetensors`)과 학습과 짝인 `system_prompt.txt`. `r`은 프로파일의 `max_lora_rank` 이하, `base_model_name_or_path`는 **서빙 프로파일의 `base_model`과 같아야** 한다. 다르면 적재가 412로 거부된다(`tests/test_vllm_backend.py::test_base_model_mismatch_rejected_before_engine`가 엔진을 올리기 전 거부를 보장한다). 프로파일: Qwen3-8B 어댑터는 `rtx4090-24g.json`(`MODEL_RUNTIME_PROFILE=/app/profiles/rtx4090-24g.json`), `gpu-12g`(Qwen3-1.7B)에는 올리지 않는다.
2. **패키지**(model-lab): 어댑터 디렉터리와 `ModelPackageManifest`(`serving_config` + `serving_config_hash`, 시스템 프롬프트 `path`/`sha256`)를 만든다. `serving_config`의 값은 실행 중인 서버에서 `curl -s http://<호스트>:<포트>/v1/info`의 `serving_config_expectation`을 읽어 그대로 맞춘다(constraint, `prompt_template_version`은 `prompt-4`, `protection_policy_version`은 `protect-2`, `output_contract`). 해시는 `canonical.sha256_hash(serving_config)`. 형식을 미리 보려면 학습 없는 대체 패키지: `.venv/bin/python scripts/make_neutral_package.py OUT_DIR --profile profiles/rtx4090-24g.json --model-config <Qwen3-8B config.json> --vllm-version <서빙 이미지의 vLLM>` (`--system-prompt-a/-b`로 프롬프트 파일을 준다).
3. **릴리스**(release-gate): 패키지를 I4에 제출해 평가(PASS)와 승인을 받고 `release_id`를 얻는다. PASS가 아닌 릴리스의 번들은 412다(HTTP.md).
4. **서빙 컨테이너**: 기반 모델 가중치가 `HF_HOME` 볼륨에 있어야 한다(오프라인이면 미리 채운다). 비밀은 파일로 준다. 예: `docker run -d --gpus all -e MODEL_RUNTIME_RELEASE_URL=<I4 주소> -e MODEL_RUNTIME_RELEASE_TOKEN_FILE=/run/secrets/rt -e MODEL_RUNTIME_TOKENS_FILE=/run/secrets/tokens -e MODEL_RUNTIME_PROFILE=/app/profiles/rtx4090-24g.json -v mr-hf:/var/lib/model-runtime/hf -v <스키마>:/schema:ro -e MODEL_RUNTIME_SCHEMA_DIR=/schema/schemas -v <비밀 디렉터리>:/run/secrets:ro model-runtime:gpu`
5. **교체(적재)**: 새 `release_id`로 `curl -s -X PUT -H "Authorization: Bearer $(cat <토큰 파일>)" http://<호스트>:<포트>/v1/releases/<release_id>`(멱등, 200 LOADED 또는 202 LOADING). `curl -s .../v1/releases/<release_id>`가 `LOADED`가 될 때까지 본다. 어댑터는 릴리스마다 따로 적재되므로(`MODEL_RUNTIME_MAX_RELEASES`, 기본 4) 이전 릴리스는 그대로 두고 새 릴리스로 서비스가 가리키는 `release_id`만 바꾸면 되돌릴 수 있다. 실패 코드: 412(설정 해시·순위·기반 모델 불일치·PASS 아님), 422(버전·무결성), 503(I4 불가·엔진). 한 턴이 통과하는지는 `POST /v1/releases/<id>/extract`로 확인한다(GPU 실측은 이 리포에서 하지 않았다, **미확인**).

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
| `ModelOutputInvalid` / 503 `OUTPUT_INVALID` | extract가 재시도를 다 써도 `ActionExtraction`을 못 지켰다. 값을 지어내지 않고 실패로 알린다(D75) | 호출자는 한 번 다시 보내고 그래도 안 되면 턴을 실패 처리한다. 잦으면 `X-Openit-Attempts`·서버 로그(`attempts=`)로 빈도를 보고 model-lab이 `extract_raw`로 형식 위반 종류를 본다. |
| `SystemPromptMismatch` / 422 `INTEGRITY_ERROR` | 어댑터 디렉터리의 시스템 프롬프트 파일이 없거나 `system_prompt.sha256`과 다르다 | 번들이 훼손됐거나 매니페스트가 어긋났다. 게시를 다시 확인한다. |
| (예외 아님) respond 대체 문장이 자주 나간다 | 모델 출력이 비어 재료를 이은 대체 문장이 나갔다(`is_fallback = true`, `X-Openit-Outcome: fallback`) | `last_call_info.protections`(G0, G5)를 집계하고 model-lab이 `respond_raw`로 보호 전 품질을 잰다. `relaxed` 목록이 크면 백엔드가 제약을 놓치는 것이므로 `supported_features`를 점검한다. 어댑터·템플릿 문제는 model-lab에 알린다. |

## 하지 말 것
- 발화나 모델 출력 원문을 로그에 남기려고 이 라이브러리의 예외·로그를 확장하지 않는다.
- 운영 중에 `serving_config`를 임의로 바꾸지 않는다. 바꾸려면 새 모델 패키지(새 해시)를 만든다.
