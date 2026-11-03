"""link_release_fetch 컴포넌트(통신, 보내는 쪽): release-gate 서버(I4)에서 릴리스 번들을 읽고 서버가 살아 있는지 본다.

번들 읽기는 계약 클라이언트(`openit_link.clients.HttpReleaseReader`)를 쓴다. 계약 타입 <-> 영역 내부 타입 변환은 이 컴포넌트의 몫이고
(번들은 manifest·adapters 경로로 내부에 넘긴다), 업무 컴포넌트는 HTTP를 모른다. 도달성 확인은 `/healthz` GET이며 사유에는 호스트:포트만 담는다(토큰·경로 없음)."""
from __future__ import annotations

import urllib.request
from typing import Callable
from urllib.parse import urlsplit

from boundary_contracts import read_secret
from boundary_contracts.clients import egress_proxy_from, require_tls_url

INPUT, OUTPUT = "PutRequest", "HttpResult"   # 호출 한 건의 이름표(번들 읽기는 릴리스 ID 하나를 받는다)


def egress_proxy_from_env(env) -> str | None:
    """공통 환경 변수 OPENIT_EGRESS_PROXY 하나만 꺼내 검증한다(없으면 None, 잘못되면 ValueError). HTTP를 아는 호출은 이 컴포넌트에만 둔다(tests/test_structure.py)."""
    return egress_proxy_from(env)


def reader_factory_from_env(env) -> Callable[[], object]:
    """환경 변수 설정으로 번들 읽기 클라이언트를 만드는 함수. 비밀(토큰)은 파일로 받는다(D84)."""
    def make():
        from openit_link.clients import HttpReleaseReader
        # 왜: 영역 밖(release-gate) 호출은 자기 영역 게이트웨이의 CONNECT 출구로만 나간다(CONVENTIONS 13절, RUNTIME R1.8). 환경을 통째로 넘기지 않고 이름 하나만 꺼낸다(R9.1)
        return HttpReleaseReader(env["MODEL_RUNTIME_RELEASE_URL"], read_secret("MODEL_RUNTIME_RELEASE_TOKEN", environ=env) or "",
                                 timeout=float(env.get("MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S", "120")), egress_proxy=egress_proxy_from(env))
    return make


def probe(release_url: str, egress_proxy: str | None = None) -> str | None:
    """release 서버에 닿으면 None, 아니면 사유(호스트:포트와 예외 이름만)."""
    try:
        require_tls_url(release_url)   # 계약 3.4.1(U99): 직접 호출도 계약 클라이언트와 같은 TLS 규칙(loopback 밖 평문 거부)
        # 왜: HTTPS_PROXY 등 환경 변수의 프록시는 읽지 않는다(계약 클라이언트와 같은 규칙). 출구가 있으면 그것만, 없으면 직접(개발)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": egress_proxy} if egress_proxy else {}))
        with opener.open(release_url.rstrip("/") + "/healthz", timeout=2):   # 소켓 timeout 2초: 도달성 확인이 /readyz 응답을 오래 붙잡지 않게. 값 2는 측정·표준 근거 없는 초기값(ADR 0012, 결과는 MODEL_RUNTIME_READY_PROBE_TTL_S 동안 캐시)
            return None
    except Exception as e:   # noqa: BLE001
        u = urlsplit(release_url)
        return f"release 서버({u.hostname}:{u.port or ''})에 닿지 못한다({type(e).__name__})"
