# model-runtime 추론 서버 이미지. 서빙 목표: RTX 4090 24GB(GPU 변형), 같은 이미지의 CPU 변형은 가짜 백엔드 스모크용.
#   GPU: docker build --build-arg VARIANT=gpu --build-context boundary=../boundary-contracts -t model-runtime:gpu .
#   CPU: docker build --build-context boundary=../boundary-contracts -t model-runtime:cpu .
# boundary-contracts는 CI에서는 태그로 설치한다(공통 규칙). 로컬 빌드의 컨텍스트는 BOUNDARY_VERSION 태그의 소스 폴더여야 한다(scripts/smoke.sh가 형제 폴더의 `git archive <태그>` 또는 GitHub 태그 clone으로 만든다. 작업 트리 상태에는 의존하지 않는다). 이 ARG가 이 리포의 계약 태그 정본이다(scripts/setup-env.sh도 여기서 읽는다).
# 의존은 잠금 파일(requirements-*.lock)로 고정한다. 비밀 값은 넣지 않는다.
# 문진 스키마를 이미지에 넣는 선택 옵션: --build-context schema=<.../survey-service/schema>(없으면 빈 스테이지라 아무것도 안 들어간다).
# 넣으면 MODEL_RUNTIME_SCHEMA_DIR 없이 /opt/survey-schema/schemas를 쓴다. 라벨 io.openit.contracts.survey-schema에 정수 버전을 밝힌다.
# (2.0.0: 모델 입출력 스키마는 boundary-contracts 패키지에 들어 있어 이 디렉터리에서 읽지 않는다. CONTRACT_VERSION만 쓴다.)
FROM scratch AS schema
FROM python:3.12-slim
ARG VARIANT=cpu
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MODEL_RUNTIME_HOST=0.0.0.0 MODEL_RUNTIME_PORT=8080 MODEL_RUNTIME_BUNDLE_DIR=/var/lib/model-runtime/bundles HF_HOME=/var/lib/model-runtime/hf
WORKDIR /app
COPY requirements-${VARIANT}.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock
# ---- 아래는 무거운 pip 레이어 뒤에 둔다(레이어 순서 중요: 위 레이어의 빌드 캐시가 재사용돼야 한다. 위쪽 명령·ENV를 바꾸지 말 것) ----
# GPU 변형에만 C 컴파일러를 넣는다. Triton이 vLLM의 LoRA 커널 등을 쓸 때 CUDA 유틸 모듈을 C 소스에서 빌드한다(enforce_eager로 피해지지 않는다고 판단, 확정 아님).
# 컴파일러 포함은 공격면을 늘리지만 vLLM 공식 이미지도 포함한다. Python 헤더는 python:3.12 이미지의 /usr/local/include/python3.12에 이미 있다(apt의 python3-dev는 다른 Python이라 넣지 않는다).
RUN if [ "$VARIANT" = "gpu" ]; then apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*; fi
# 캐시·홈·원격 측정 기본값(읽기 전용 루트에서도 쓸 수 있게 모두 /var/lib/model-runtime 아래의 쓰기 가능한 경로). compose가 같은 변수로 덮어쓸 수 있다.
ENV CC=gcc HOME=/var/lib/model-runtime/home XDG_CACHE_HOME=/var/lib/model-runtime/cache VLLM_CACHE_ROOT=/var/lib/model-runtime/cache/vllm \
    TORCHINDUCTOR_CACHE_DIR=/var/lib/model-runtime/cache/torchinductor TRITON_CACHE_DIR=/var/lib/model-runtime/cache/triton \
    TORCH_HOME=/var/lib/model-runtime/cache/torch VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1
COPY --from=boundary . /tmp/boundary-contracts
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY profiles /app/profiles
COPY --from=schema / /opt/survey-schema
RUN pip install --no-deps /tmp/boundary-contracts && pip install --no-deps /app && rm -rf /tmp/boundary-contracts \
    && useradd --uid 10001 --create-home runtime \
    && mkdir -p /var/lib/model-runtime/bundles /var/lib/model-runtime/hf /var/lib/model-runtime/home /var/lib/model-runtime/cache/vllm \
       /var/lib/model-runtime/cache/torchinductor /var/lib/model-runtime/cache/triton /var/lib/model-runtime/cache/torch \
    && chown -R 10001 /var/lib/model-runtime
# ---- 라벨 블록은 맨 뒤에 둔다: REVISION(커밋마다)·버전이 바뀌어도 위의 무거운 레이어(pip, apt)의 빌드 캐시가 재사용돼야 한다. 이 위로 옮기지 말 것 ----
ARG REVISION=unknown
ARG SERVICE_NAME   # 기본값 없음: 라벨 io.openit.service는 리포 이름이 아니라 서비스(이미지) 이름이다(model-serving, GPU 변형은 model-serving-gpu). 영역 빌드 진입점 scripts/build-image.sh가 variant별로 넘긴다
ARG BOUNDARY_VERSION=v3.0.2
ARG MODEL_RUNTIME_VERSION   # 기본값 없음: 비면 아래 RUN이 빌드를 실패시킨다(D50 라벨 규칙). 정본은 pyproject.toml의 version(= session.py RUNTIME_VERSION). 라벨 값은 v 접두사를 붙인다(ImageLabels, D108). scripts/smoke.sh가 pyproject에서 읽어 v를 붙여 넘긴다
# 값이 없다는 표기는 n/a 하나다(계약 ImageLabels: none·unknown 불가)
ARG SURVEY_SCHEMA_VERSION=n/a
LABEL io.openit.service="${SERVICE_NAME}" \
      io.openit.revision="${REVISION}" \
      io.openit.contracts.boundary="${BOUNDARY_VERSION}" \
      io.openit.contracts.survey-schema="${SURVEY_SCHEMA_VERSION}" \
      io.openit.model-runtime="${MODEL_RUNTIME_VERSION}"
USER 10001
# 라벨 인자 가드(가벼운 레이어라 캐시에 영향 없음): 인자가 비면 unknown 같은 거짓 라벨 대신 빌드를 실패시킨다
RUN test -n "${SERVICE_NAME}" || { echo "빌드 인자 SERVICE_NAME이 비었다(io.openit.service 라벨). scripts/build-image.sh가 넘긴다" >&2; exit 1; }; test -n "${MODEL_RUNTIME_VERSION}" || { echo "빌드 인자 MODEL_RUNTIME_VERSION이 비었다(D50 라벨). v + pyproject.toml의 version을 넘긴다" >&2; exit 1; }
# 이 디렉터리들은 serving-data 볼륨을 /var/lib/model-runtime에 마운트하면 가려진다. 그래서 서버가 시작할 때 mkdir -p로 다시 보장하고 쓸 수 없으면 /readyz 503 reason을 낸다.
# 번들 캐시 디렉터리는 10001 소유로 만들어 둔다(새 이름 있는 볼륨은 이 소유권을 물려받는다). 바인드 마운트라면 호스트에서 소유자를 맞춘다.
# 설정은 환경 변수로만 받는다(src/model_runtime/server.py 머리말). 스키마 디렉터리(MODEL_RUNTIME_SCHEMA_DIR)와 어댑터는 읽기 전용으로 마운트한다.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('MODEL_RUNTIME_PORT','8080')+'/healthz')"
CMD ["python", "-m", "model_runtime.server"]
