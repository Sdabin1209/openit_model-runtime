"""link_release_fetch 컴포넌트(통신, 보내는 쪽): release-gate 서버(I4)에서 릴리스 번들을 읽고 서버가 살아 있는지 본다.

번들 읽기는 계약 클라이언트(`openit_link.clients.HttpReleaseReader`)를 쓴다. 계약 타입 <-> 영역 내부 타입 변환은 이 컴포넌트의 몫이고
(번들은 manifest·adapters 경로로 내부에 넘긴다), 업무 컴포넌트는 HTTP를 모른다. 도달성 확인은 `/healthz` GET이며 사유에는 호스트:포트만 담는다(토큰·경로 없음)."""
from __future__ import annotations

import urllib.request
from typing import Callable
from urllib.parse import urlsplit

from boundary_contracts import read_secret

INPUT, OUTPUT = "PutRequest", "HttpResult"   # 호출 한 건의 이름표(번들 읽기는 릴리스 ID 하나를 받는다)


def reader_factory_from_env(env) -> Callable[[], object]:
    """환경 변수 설정으로 번들 읽기 클라이언트를 만드는 함수. 비밀(토큰)은 파일로 받는다(D84)."""
    def make():
        from openit_link.clients import HttpReleaseReader
        return HttpReleaseReader(env["MODEL_RUNTIME_RELEASE_URL"], read_secret("MODEL_RUNTIME_RELEASE_TOKEN", environ=env) or "",
                                 timeout=float(env.get("MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S", "120")))
    return make


def probe(release_url: str) -> str | None:
    """release 서버에 닿으면 None, 아니면 사유(호스트:포트와 예외 이름만)."""
    try:
        with urllib.request.urlopen(release_url.rstrip("/") + "/healthz", timeout=2):
            return None
    except Exception as e:   # noqa: BLE001
        u = urlsplit(release_url)
        return f"release 서버({u.hostname}:{u.port or ''})에 닿지 못한다({type(e).__name__})"
