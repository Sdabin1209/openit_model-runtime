"""시험 공통 설정.

왜: 서버는 시작할 때 환경 변수를 계약 ModelServingEnv 선언으로 검증하고(U73, ADR 0032), 선언은 MODEL_RUNTIME_RELEASE_URL을 필수로 한다.
릴리스 읽기기를 직접 넣는 시험(reader_factory=...)은 I4 서버 없이 도는 것이 목적이라 주소가 환경에 없다. 그런 시험에만 자리 표시 주소를 채우고,
그 주소로는 도달성 확인을 하지 않게 한다. 제품 코드에는 이 시험용 예외가 없다(선언을 그대로 검증한다). 주소를 직접 주는 시험과
선언 검증 자체를 시험하는 시험(tests/test_env_check.py)에는 손대지 않는다."""
import pytest

from model_runtime import server
from model_runtime.wiring import service as service_module

PLACEHOLDER_URL = "http://127.0.0.1:9"   # discard 포트. 도달성 확인은 아래에서 끈다


@pytest.fixture(autouse=True)
def _release_url_for_injected_readers(monkeypatch, request):
    if request.module.__name__.endswith("test_env_check"):
        return
    real = server.build_service

    def build_service(env, *, reader_factory=None, **kw):
        env = dict(env)
        if "MODEL_RUNTIME_RELEASE_URL" not in env:
            env["MODEL_RUNTIME_RELEASE_URL"] = PLACEHOLDER_URL
            if reader_factory is not None:
                monkeypatch.setattr(service_module, "probe", lambda url, egress_proxy=None: None)
        return real(env, **({"reader_factory": reader_factory} if reader_factory is not None else {}), **kw)

    monkeypatch.setattr(server, "build_service", build_service)
