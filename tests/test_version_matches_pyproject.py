"""pyproject.toml 의 version 과 실행 중 버전 상수(RUNTIME_VERSION)가 같다. 판을 올릴 때 하나만 바꾸면 이미지 라벨이 거짓이 된다(0.11.27)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_equals_pyproject_version():
    pv = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    rv = re.search(r'^RUNTIME_VERSION = "([^"]+)"', (ROOT / "src/model_runtime/wiring/session.py").read_text(encoding="utf-8"), re.M).group(1)
    assert rv == pv
