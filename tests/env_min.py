"""하위 프로세스에 줄 최소 환경(RUNTIME R9.1, C45): 상속받은 환경을 통째로 넘기지 않고 허용 목록의 이름만 담는다. 시험의 모든 subprocess 호출이 이것을 쓴다(같은 뜻 한 번만).
기본 목록은 head check-container-inputs 와 같은 최소 집합이다. 그 호출이 실제로 필요로 하는 이름만 extra 로 더한다. ssh=True 는 git+ssh 로 내려받는 호출(SSH 에이전트 소켓)에만 쓴다."""
import os

ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_CONFIG", "DOCKER_HOST")
SSH_NAMES = ("SSH_AUTH_SOCK",)


def min_env(extra=None, *, ssh=False):
    env = {k: os.environ[k] for k in ALLOWED + (SSH_NAMES if ssh else ()) if k in os.environ}
    env.update(extra or {})
    return env
