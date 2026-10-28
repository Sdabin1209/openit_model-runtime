"""버전의 정본은 pyproject.toml의 version 하나이고 RUNTIME_VERSION·설치 메타데이터가 같아야 한다(스모크 라벨도 pyproject에서 읽는다)."""
import tomllib
import unittest
from pathlib import Path

import model_runtime as mr

ROOT = Path(__file__).resolve().parent.parent


class VersionSource(unittest.TestCase):
    def test_runtime_version_matches_pyproject(self):
        pin = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(mr.RUNTIME_VERSION, pin)


if __name__ == "__main__":
    unittest.main()
