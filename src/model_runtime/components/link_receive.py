"""link_receive 컴포넌트(통신, 받는 쪽): model-runtime 추론 서버의 HTTP 면 (boundary-contracts/HTTP.md "I9 ModelRuntime 추론 서버", D62, D118).

경로: PUT /v1/releases/{id}(적재 신호, 멱등) · GET /v1/releases/{id}(상태) · POST /v1/releases/{id}/extract(LoRA-A, JSON)|respond(LoRA-B, text/plain) ·
GET /v1/info · GET /healthz · GET /readyz.
인증 401/403, 본문 상한 413, 요청 계약 검증 400, BoundaryError 본문, /healthz·/readyz는 통신 기반 라이브러리(openit_link.App)가 한다(다시 구현하지 않는다).
이 컴포넌트는 경로와 역할(누가 어느 경로를 부를 수 있나), 요청을 release_registry 작업 하나로 바꾸는 것, 결과를 응답으로 바꾸는 것만 안다.
release_registry는 HTTP를 모르고, 이 파일은 업무 규칙을 두지 않는다. 서버(waitress) 구성값의 근거: ADR 0022."""
from __future__ import annotations

import logging
import math
from typing import Mapping

import waitress
from boundary_contracts.errors import BoundaryError
from openit_link import App, Request, Response, Route

from ..wiring.schema_gate import violations

INPUT, OUTPUT = "InferRequest", "HttpResult"
log = logging.getLogger("model_runtime.server")
MAX_BODY = 4 * 1024 * 1024      # 본문 상한. 입력 상한(MAX_INPUT_CHARS)이 더 작아 이 값은 파싱 전 거친 방어선이다 (ADR 0012, 4MiB 근거 기록 없음)
MAX_CONNECTIONS = 32            # 왜: 엔진 호출이 직렬이라(ADR 0005) 많이 받아도 대기만 길어진다. 값 32는 초기값, 부하 측정 안 함 (ADR 0012)
INFER_DENY_ROLES = frozenset({"loader"})   # 적재 전용 역할은 extract/respond를 못 한다


def _rid(req: Request) -> str:
    rid = req.params["release_id"]
    if violations("PutRequest", {"release_id": rid}):   # release_id 형식은 스키마 PutRequest가 정한다
        raise BoundaryError("INVALID_RECORD", "release_id 형식 위반")
    return rid


def _resp(result: tuple) -> Response:
    status, body, *rest = result
    return Response(status, body, rest[0] if rest else {})


def make_app(svc) -> App:
    """추론 서버의 WSGI 앱(PEP 3333). svc는 release_registry.Service. 토큰이 없으면(개발용 무인증) 모든 경로가 공개다."""
    role_of = {tok: role for role, tok in svc.tokens.items()}
    anyone = frozenset(svc.tokens) if svc.tokens else frozenset()
    put_roles = anyone if svc.put_roles is None else anyone & svc.put_roles
    infer_roles = anyone - INFER_DENY_ROLES

    def put(req: Request) -> Response:
        return _resp(svc.put(_rid(req)))

    def get(req: Request) -> Response:
        return Response(200, svc.get(_rid(req)))

    def info(req: Request) -> Response:
        return Response(200, svc.info())

    def extract(req: Request) -> Response:
        return _resp(svc.infer(_rid(req), "extract", req.body))

    def respond(req: Request) -> Response:
        return _resp(svc.infer(_rid(req), "respond", req.body))

    def ready():
        ok, why = svc.readiness()
        # 인증을 켠 실행의 /readyz는 인증하지 않으므로 사유를 응답에 싣지 않는다(내부 주소·예외 이름 노출 최소화, MR-10). 사유는 로그에만 남긴다(주소의 호스트:포트와 예외 이름뿐)
        if not ok:
            log.warning("not ready: %s", why)
        if ok:
            return True, {"status": "ready"}
        return False, ({"status": "not ready"} if svc.tokens else {"status": "not ready", "reason": why})   # 인증이 꺼진 개발용 실행(토큰 없음)에서는 이전처럼 사유를 응답에 싣는다

    routes = [
        Route("GET", "/v1/info", info, roles=anyone),
        Route("PUT", "/v1/releases/{release_id}", put, roles=put_roles, max_body=MAX_BODY),
        Route("GET", "/v1/releases/{release_id}", get, roles=anyone),
        Route("POST", "/v1/releases/{release_id}/extract", extract, roles=infer_roles, request_schema="ExtractRequest", response_schema="ActionExtraction", max_body=MAX_BODY),
        Route("POST", "/v1/releases/{release_id}/respond", respond, roles=infer_roles, request_schema="ResponseRequest", max_body=MAX_BODY),
    ]
    return _guard(App(routes, role_of, ready=ready))


def _guard(app: App):
    """통신 기반 라이브러리가 아직 못 다루는 하나를 막는다: 깊게 중첩된 JSON은 파이썬 JSON 해석기가 RecursionError를 내는데 라이브러리는 ValueError만 잡는다.
    앱이 응답을 시작하기 전에 난 오류라서 여기서 계약대로 400 INVALID_RECORD를 낸다(변경 요청: docs/change-requests/CR-007-link-json-recursion.md)."""
    def wsgi(environ, start_response):
        try:
            return app(environ, start_response)
        except RecursionError:
            status, headers, data = app._error(BoundaryError("INVALID_RECORD", "본문이 JSON이 아니다"))
            start_response(f"{status} Bad Request", list(headers.items()))
            return [data]
    return wsgi


def make_server(svc, host: str, port: int, *, max_connections: int = MAX_CONNECTIONS):
    """waitress 서버(아직 시작 전). 값의 출처는 ADR 0022.
    threads=max_connections: 추론 요청 하나가 엔진 직렬화 락을 기다리며 스레드를 최대 호출 상한(3.0초)까지 쥐므로, 스레드가 연결 수보다 적으면 /healthz가 그 뒤에 줄 선다.
    channel_timeout: 헤더를 끝내지 않거나 유휴인 연결을 닫는다(waitress는 정수 초만 받는다). cleanup_interval 1초: 기본 30초면 실제로 닫히는 시각이 timeout보다 최대 30초 늦다.
    max_request_body_size=MAX_BODY: 이 크기를 넘는 본문은 waitress가 413으로 거절한다(읽기 전에)."""
    return waitress.create_server(make_app(svc), host=host, port=port, threads=max_connections, connection_limit=max_connections,
                                  channel_timeout=max(1, math.ceil(svc.conn_timeout_s)), cleanup_interval=1, max_request_body_size=MAX_BODY,
                                  ident="model-runtime")
