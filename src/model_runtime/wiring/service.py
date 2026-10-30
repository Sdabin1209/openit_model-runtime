"""릴리스 서비스 조립: release_registry에 세션 적재(session.load), 이웃 서버 도달성(link_release_fetch), 시작 점검(runtime_dirs_check), 버전을 주입한다.
컴포넌트끼리는 서로를 모르므로 이 연결은 wiring의 몫이다(규칙 5)."""
from __future__ import annotations

from ..components import runtime_dirs_check
from ..components.constraint_relax import RELAX_POLICY_VERSION
from ..components.fallback_text import PROTECTION_POLICIES
from ..components.prompt_render import DEFAULT_TEMPLATE_VERSION, DEPRECATED_TEMPLATE_VERSIONS, SUPPORTED_TEMPLATE_VERSIONS
from ..components.link_release_fetch import probe
from ..components.release_registry import Service as _Registry
from .schema_gate import gate
from .session import RUNTIME_VERSION, load

_dirs_check = gate(runtime_dirs_check)


class Service(_Registry):
    def __init__(self, **kw):
        kw.setdefault("load_session", load)
        kw.setdefault("probe", probe)
        kw.setdefault("dirs_check", _dirs_check)
        kw.setdefault("runtime_version", RUNTIME_VERSION)
        kw.setdefault("facts", {"relax_policy_version": RELAX_POLICY_VERSION, "template_versions": SUPPORTED_TEMPLATE_VERSIONS,
                                "template_version_default": DEFAULT_TEMPLATE_VERSION, "deprecated_template_versions": DEPRECATED_TEMPLATE_VERSIONS,
                                "protection_policy_versions": PROTECTION_POLICIES})   # info()가 알리는 이 서버의 지원 목록(컴포넌트들의 정본에서 읽는다)
        super().__init__(**kw)
