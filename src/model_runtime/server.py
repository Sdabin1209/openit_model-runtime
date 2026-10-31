"""model-runtime 추론 서버의 진입점·설정 읽기 (boundary-contracts/HTTP.md "I9 ModelRuntime 추론 서버", D62). 컴포넌트가 아니며 업무 규칙을 두지 않는다.
HTTP 면은 components/link_receive(통신 기반 라이브러리 openit_link 위), 적재·추론은 components/release_registry, 연결은 wiring/service다. 환경 변수는 이 파일만 읽는다.

경로: PUT /v1/releases/{id}(적재 신호, 멱등) · GET /v1/releases/{id}(상태) · POST /v1/releases/{id}/extract(LoRA-A, JSON)|respond(LoRA-B, text/plain) ·
GET /v1/info · GET /healthz · GET /readyz. 서버가 I4(HttpReleaseReader)에서 번들을 스스로 읽어 적재한다.
적재 실패는 이미 적재된 릴리스를 건드리지 않는다. 결과는 응답 헤더 X-Openit-Outcome(extract는 ok, respond는 ok|fallback)|Attempts|Protections.
extract가 재시도 후에도 ActionExtraction을 지키지 못하면 값을 지어내지 않고 503 UNAVAILABLE(details.reason=OUTPUT_INVALID, D75)을 낸다.
시스템 프롬프트는 적재한 릴리스 패키지(어댑터 디렉터리의 파일)의 것만 쓴다(D75).
엔진 호출은 직렬화하고, 대기가 길 것 같으면 큐에 쌓지 않고 503으로 거절한다. 발화·출력 원문은 로그에 남기지 않는다.

환경 변수(진입점만 읽는다): MODEL_RUNTIME_BACKEND(vllm|fake|mimic, 기본 vllm; mimic은 데모용 모방 컴포넌트, MODEL_RUNTIME_ALLOW_FAKE=1 필요, 흐름 파일은 MODEL_RUNTIME_MIMIC_FLOW_FILE 또는 MODEL_RUNTIME_FAKE_FLOW_FILE, 규칙표는 MODEL_RUNTIME_MIMIC_RULES_FILE), MODEL_RUNTIME_SCHEMA_DIR(.../schema/schemas; 없으면 이미지에 넣은 /opt/survey-schema/schemas),
MODEL_RUNTIME_RELEASE_URL·MODEL_RUNTIME_RELEASE_TOKEN(I4 서버; 주소에 닿지 못하면 /readyz 503과 사유, /healthz는 200), MODEL_RUNTIME_READY_PROBE_TTL_S(도달성 확인 캐시, 기본 5), MODEL_RUNTIME_BUNDLE_DIR(기본 /var/lib/model-runtime/bundles; 쓸 수 없으면 /readyz 503과 이유),
MODEL_RUNTIME_FAKE_FLOW_FILE(fake 전용: 문항 ID 목록 JSON 또는 {"order","slots","skips","corrections","reasks"} 객체; LORA_A가 입력 question_id의 다음 문항으로 가는 NEXT를 낸다. slots가 있으면 발화에서 첫 칸의 값도 낸다, 0.7.1), MODEL_RUNTIME_FAKE_EXTRACTION(fake 전용: LORA_A 고정 출력 JSON, flow보다 우선),
MODEL_RUNTIME_PROFILE(프로파일 JSON, vllm은 base_model 필요), MODEL_RUNTIME_BASE_MODEL(프로파일의 base_model을 덮어씀; 어댑터의 기반 모델과 같아야 함),
HF_HOME·HF_HUB_OFFLINE(Hugging Face 표준 변수: 모델 캐시 위치와 오프라인; 이 서버가 읽지 않고 vLLM이 읽는다), MODEL_RUNTIME_TOKENS(역할=토큰,...; 있으면 /v1/*에 Bearer 필요)
비밀(MODEL_RUNTIME_TOKENS, MODEL_RUNTIME_RELEASE_TOKEN)은 D84에 따라 <이름>_FILE(파일 경로)을 우선 읽고 없을 때만 환경 변수를 쓴다(boundary_contracts.read_secret),
MODEL_RUNTIME_EXTRACT_DEADLINE_S(기본 3.0)·MODEL_RUNTIME_RESPOND_DEADLINE_S(기본 5.0)(호출 하나의 총 상한: 대기+추론+재시도; 넘기면 503 ENGINE_TIMEOUT. 계약 CONVENTIONS '시간 상한 사슬' 표 값(D119), 측정하지 않은 값: ADR 0030(0020 대체). 옛 MODEL_RUNTIME_REQUEST_DEADLINE_S는 시작 거부), MODEL_RUNTIME_BACKEND_RETRY_S(백엔드 기동 실패 후 재시도 간격, 기본 30, 0이면 재시도 없음), MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S(I4 번들 읽기 소켓 timeout, 기본 120),
MODEL_RUNTIME_QUEUE_WAIT_S(기본 3.0), MODEL_RUNTIME_PUT_WAIT_S(기본 3.0), MODEL_RUNTIME_MAX_RELEASES(기본 4),
MODEL_RUNTIME_HOST/PORT(기본 127.0.0.1:8080, 이미지는 0.0.0.0), MODEL_RUNTIME_ALLOW_NO_AUTH=1(개발용: 토큰 없이 시작), MODEL_RUNTIME_ALLOW_FAKE=1(fake 백엔드 허용),
MODEL_RUNTIME_PUT_ROLES(적재 PUT을 허용할 역할 목록, 없으면 인증된 모든 역할), MODEL_RUNTIME_MAX_INPUT_CHARS(기본 2500), MODEL_RUNTIME_MAX_CONNECTIONS(32), MODEL_RUNTIME_MAX_LOADING(2),
MODEL_RUNTIME_CONN_TIMEOUT_S(15), MODEL_RUNTIME_SHUTDOWN_GRACE_S(5). 각 기본값의 근거: docs/adr/."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Callable, Mapping

from boundary_contracts import SecretError, read_secret, secret_source
from boundary_contracts.errors import BoundaryError

from .components.fake_backend import FakeBackend, make_default_responder
from .components.flow_file_load import load_fake_corrections, load_fake_flow, load_fake_flow_spec, load_fake_reasks
from .components.link_receive import INFER_DENY_ROLES, MAX_BODY, MAX_CONNECTIONS, make_app, make_server
from .components.link_release_fetch import reader_factory_from_env
from .components.release_registry import (CONN_TIMEOUT_S, DEFAULT_DEADLINES_S, MAX_FAILED_KEPT, MAX_INPUT_CHARS, MAX_LOADING, Release, STATUS, to_boundary)
from .wiring.backends import mimic_backend_from_files
from .wiring.schema_gate import gate
from .wiring.service import Service

log = logging.getLogger("model_runtime.server")
DEFAULT_BUNDLE_DIR = "/var/lib/model-runtime/bundles"
EMBEDDED_SCHEMA_DIR = "/opt/survey-schema/schemas"   # 이미지에 스키마를 넣어 빌드한 경우(Dockerfile의 schema 컨텍스트)
from .components import token_table as _tt
_token_table = gate(_tt, ValueError)


def build_service(env: Mapping[str, str], *, reader_factory: Callable[[], object] | None = None, clock=None) -> Service:   # clock: 시험이 가짜 시계를 넣는 자리(없으면 실제 시계, ADR 0029)
    backend_name = env.get("MODEL_RUNTIME_BACKEND", "vllm")
    schema_dir = env.get("MODEL_RUNTIME_SCHEMA_DIR") or (EMBEDDED_SCHEMA_DIR if Path(EMBEDDED_SCHEMA_DIR).is_dir() else None)
    if not schema_dir:
        raise SystemExit("MODEL_RUNTIME_SCHEMA_DIR가 필요하다(이미지에 스키마가 들어 있지 않다)")
    try:   # 비밀은 파일로 받는다(D84): MODEL_RUNTIME_TOKENS_FILE 우선, 없으면 MODEL_RUNTIME_TOKENS(개발·시험용)
        raw_tokens = read_secret("MODEL_RUNTIME_TOKENS", environ=env) or ""
    except SecretError as e:
        raise SystemExit(str(e))   # 메시지에는 이름과 경로만 있다
    try:   # 인증은 기본 닫힘(MR-1): 토큰이 없으면 시작을 거부한다. 개발용으로만 MODEL_RUNTIME_ALLOW_NO_AUTH=1
        tokens = _token_table({"raw": raw_tokens, "allow_empty": env.get("MODEL_RUNTIME_ALLOW_NO_AUTH") == "1"})
    except ValueError as e:
        raise SystemExit(str(e))
    if not tokens:
        log.warning("인증 없이 시작한다(MODEL_RUNTIME_ALLOW_NO_AUTH=1, 개발용)")
    log.info("비밀 출처(값 아님): MODEL_RUNTIME_TOKENS=%s MODEL_RUNTIME_RELEASE_TOKEN=%s",
             secret_source("MODEL_RUNTIME_TOKENS", environ=env), secret_source("MODEL_RUNTIME_RELEASE_TOKEN", environ=env))
    if env.get("MODEL_RUNTIME_REQUEST_DEADLINE_S"):   # 조용히 무시하면 옛 3.5초 하나가 남아 있다고 착각한다
        raise SystemExit("MODEL_RUNTIME_REQUEST_DEADLINE_S는 없어졌다(계약 2.2.0). 호출별로 MODEL_RUNTIME_EXTRACT_DEADLINE_S(3.0), MODEL_RUNTIME_RESPOND_DEADLINE_S(5.0)를 쓴다")
    if env.get("MODEL_RUNTIME_FAKE_RISK_PHRASE"):
        raise SystemExit("MODEL_RUNTIME_FAKE_RISK_PHRASE는 계약 2.0.0에서 없어졌다(v2 출력에 위험 신호 표현이 없다). 설정을 지운다")
    if backend_name == "fake":
        if env.get("MODEL_RUNTIME_ALLOW_FAKE") != "1":   # 운영에서 가짜 백엔드로 뜨는 사고를 막는다(실수로 BACKEND=fake가 들어가도 시작 거부)
            raise SystemExit("MODEL_RUNTIME_BACKEND=fake는 개발·시험용이다. 의도한 것이면 MODEL_RUNTIME_ALLOW_FAKE=1을 함께 준다")
        try:
            responder = make_default_responder(
                load_fake_flow(env["MODEL_RUNTIME_FAKE_FLOW_FILE"]) if env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") else None,
                json.loads(env["MODEL_RUNTIME_FAKE_EXTRACTION"]) if env.get("MODEL_RUNTIME_FAKE_EXTRACTION") else None,
                *(load_fake_flow_spec(env["MODEL_RUNTIME_FAKE_FLOW_FILE"]) if env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") else ({}, [])),
                load_fake_corrections(env["MODEL_RUNTIME_FAKE_FLOW_FILE"]) if env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") else (),
                load_fake_reasks(env["MODEL_RUNTIME_FAKE_FLOW_FILE"]) if env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") else ())
        except (OSError, ValueError) as e:
            raise SystemExit(f"fake 출력 설정이 틀렸다(MODEL_RUNTIME_FAKE_FLOW_FILE / MODEL_RUNTIME_FAKE_EXTRACTION): {type(e).__name__}")
        factory = lambda: FakeBackend(responder)
    elif backend_name == "mimic":
        if env.get("MODEL_RUNTIME_ALLOW_FAKE") != "1":   # 모방도 모델이 아니다: fake와 같은 이유로 의도 표시가 있어야 뜬다(ADR 0009)
            raise SystemExit("MODEL_RUNTIME_BACKEND=mimic은 데모·시험용이다. 의도한 것이면 MODEL_RUNTIME_ALLOW_FAKE=1을 함께 준다")
        try:   # 같은 입력 형식(순서·칸·건너뛰기·정정·되묻기)을 가짜와 공유한다. 규칙표는 데이터 파일이다
            mimic = mimic_backend_from_files(env.get("MODEL_RUNTIME_MIMIC_FLOW_FILE") or env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") or None,
                                             env.get("MODEL_RUNTIME_MIMIC_RULES_FILE") or None)
        except (OSError, ValueError) as e:
            raise SystemExit(f"mimic 설정이 틀렸다(MODEL_RUNTIME_MIMIC_FLOW_FILE / MODEL_RUNTIME_MIMIC_RULES_FILE): {type(e).__name__}")
        factory = lambda: mimic
    elif backend_name == "vllm":
        def factory():
            from .components.vllm_backend import VLLMBackend, load_profile
            prof = load_profile(env["MODEL_RUNTIME_PROFILE"]) if env.get("MODEL_RUNTIME_PROFILE") else {}
            for k, v in prof.get("env", {}).items():   # 프로파일이 정한 엔진용 환경(예: VLLM_USE_FLASHINFER_SAMPLER)
                if not k.startswith(("_",)):
                    os.environ.setdefault(k, str(v))
            return VLLMBackend(None, profile=prof, base_model=env.get("MODEL_RUNTIME_BASE_MODEL") or None)
    else:
        raise SystemExit("MODEL_RUNTIME_BACKEND는 vllm, fake, mimic 중 하나")
    custom_reader = reader_factory
    if reader_factory is None:
        reader_factory = reader_factory_from_env(env)
    offline = env.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes")
    hf_home = None if (backend_name != "vllm" or offline) else (env.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"))
    dirs = [env[k] for k in ("HOME", "XDG_CACHE_HOME", "VLLM_CACHE_ROOT", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR", "TORCH_HOME")
            if env.get(k)]   # 환경 변수가 정한 것만 보장한다(이미지는 ENV로 모두 정해 둔다)
    return Service(**({"clock": clock} if clock is not None else {}), schema_dir=Path(schema_dir), hf_home=hf_home, runtime_dirs=sorted(set(dirs)), release_url=env.get("MODEL_RUNTIME_RELEASE_URL") or None,
                   release_required=custom_reader is None, probe_ttl_s=float(env.get("MODEL_RUNTIME_READY_PROBE_TTL_S", "5")),
                   bundle_dir=Path(env.get("MODEL_RUNTIME_BUNDLE_DIR", DEFAULT_BUNDLE_DIR)),
                   base_backend_factory=factory, reader_factory=reader_factory, backend_name=backend_name, tokens=tokens,
                   queue_wait_s=float(env.get("MODEL_RUNTIME_QUEUE_WAIT_S", "3.0")),
                   put_wait_s=float(env.get("MODEL_RUNTIME_PUT_WAIT_S", "3.0")),
                   deadlines={"extract": float(env.get("MODEL_RUNTIME_EXTRACT_DEADLINE_S", DEFAULT_DEADLINES_S["extract"])),
                              "respond": float(env.get("MODEL_RUNTIME_RESPOND_DEADLINE_S", DEFAULT_DEADLINES_S["respond"]))},
                   backend_retry_s=float(env.get("MODEL_RUNTIME_BACKEND_RETRY_S", "30")),
                   max_releases=int(env.get("MODEL_RUNTIME_MAX_RELEASES", "4")),
                   max_input_chars=int(env.get("MODEL_RUNTIME_MAX_INPUT_CHARS", str(MAX_INPUT_CHARS))),
                   max_loading=int(env.get("MODEL_RUNTIME_MAX_LOADING", str(MAX_LOADING))),
                   put_roles=(frozenset(x.strip() for x in env["MODEL_RUNTIME_PUT_ROLES"].split(",") if x.strip()) if env.get("MODEL_RUNTIME_PUT_ROLES") else None),
                   conn_timeout_s=float(env.get("MODEL_RUNTIME_CONN_TIMEOUT_S", str(CONN_TIMEOUT_S))))


def main(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    svc = build_service(env)
    host, port = env.get("MODEL_RUNTIME_HOST", "127.0.0.1"), int(env.get("MODEL_RUNTIME_PORT", "8080"))   # 기본은 루프백. 컨테이너 이미지가 0.0.0.0을 준다
    srv = make_server(svc, host, port, max_connections=int(env.get("MODEL_RUNTIME_MAX_CONNECTIONS", str(MAX_CONNECTIONS))))
    log.info("대기 %s:%s backend=%s", host, srv.effective_port, svc.backend_name)

    def stop(*_):   # 우아한 종료: waitress의 run()이 SystemExit을 받으면 새 연결을 그만 받고 진행 중인 요청을 마칠 때까지 기다린 뒤 돌아온다
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        srv.run()
    finally:
        srv.close()
        svc.drain(float(env.get("MODEL_RUNTIME_SHUTDOWN_GRACE_S", "5")))
        svc.close()


if __name__ == "__main__":
    main()
