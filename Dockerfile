# model-runtime 추론 서버 이미지. 서빙 목표: RTX 4090 24GB(GPU 변형), 같은 이미지의 CPU 변형은 가짜 백엔드 스모크용.
#   GPU: docker build --build-arg VARIANT=gpu --build-context boundary=../boundary-contracts -t model-runtime:gpu .
#   CPU: docker build --build-context boundary=../boundary-contracts -t model-runtime:cpu .
# boundary-contracts는 CI에서는 태그로 설치한다(공통 규칙). 로컬 빌드는 형제 폴더를 읽기 전용 빌드 컨텍스트로 쓴다.
# 의존은 잠금 파일(requirements-*.lock)로 고정한다. 비밀 값은 넣지 않는다.
# 문진 스키마를 이미지에 넣는 선택 옵션: --build-context schema=<.../survey-service/schema>(없으면 빈 스테이지라 아무것도 안 들어간다).
# 넣으면 MODEL_RUNTIME_SCHEMA_DIR 없이 /opt/survey-schema/schemas를 쓴다. 라벨 io.openit.contracts.survey-schema에 정수 버전을 밝힌다.
FROM scratch AS schema
FROM python:3.12-slim
ARG VARIANT=cpu
ARG REVISION=unknown
ARG BOUNDARY_VERSION=unknown
ARG SURVEY_SCHEMA_VERSION=unknown
ARG TURN_ENGINE_VERSION=none
LABEL io.openit.service="model-runtime" \
      io.openit.revision="${REVISION}" \
      io.openit.contracts.boundary="${BOUNDARY_VERSION}" \
      io.openit.contracts.survey-schema="${SURVEY_SCHEMA_VERSION}" \
      io.openit.turn-engine="${TURN_ENGINE_VERSION}" \
      io.openit.model-runtime="0.5.1"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MODEL_RUNTIME_HOST=0.0.0.0 MODEL_RUNTIME_PORT=8080 MODEL_RUNTIME_BUNDLE_DIR=/var/lib/model-runtime/bundles
WORKDIR /app
COPY requirements-${VARIANT}.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock
COPY --from=boundary . /tmp/boundary-contracts
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY profiles /app/profiles
COPY --from=schema / /opt/survey-schema
RUN pip install --no-deps /tmp/boundary-contracts && pip install --no-deps /app && rm -rf /tmp/boundary-contracts \
    && useradd --uid 10001 --create-home runtime \
    && mkdir -p /var/lib/model-runtime/bundles && chown -R 10001 /var/lib/model-runtime
USER 10001
# 번들 캐시 디렉터리는 10001 소유로 만들어 둔다(새 이름 있는 볼륨은 이 소유권을 물려받는다). 바인드 마운트라면 호스트에서 소유자를 맞춘다.
# 설정은 환경 변수로만 받는다(src/model_runtime/server.py 머리말). 스키마 디렉터리(MODEL_RUNTIME_SCHEMA_DIR)와 어댑터는 읽기 전용으로 마운트한다.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('MODEL_RUNTIME_PORT','8080')+'/healthz')"
CMD ["python", "-m", "model_runtime.server"]
