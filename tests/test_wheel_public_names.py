"""재발 방지(0.11.4): wheel을 실제로 빌드해 새 venv에 설치하고, 다른 영역이 쓰는 공개 이름이 설치물에서 import되는지 본다.
왜: pyproject의 패키지 목록을 손으로 써서 model_runtime.prompts·schema_guard가 wheel에서 빠졌는데, 시험은 src 레이아웃(pythonpath=src)으로 돌아 못 잡았다.
의존 패키지(boundary_contracts 등)는 이 시험을 도는 환경의 site-packages를 PYTHONPATH로 빌려 쓴다(새 venv에는 wheel만 --no-deps로 설치). 빌드는 setuptools를 받으므로 네트워크가 필요하다."""
import json
import shutil
import site
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import json, importlib
import model_runtime as m
out = {"file": m.__file__, "missing": [n for n in m.__all__ if not hasattr(m, n)], "submodules": {}}
for mod in ("model_runtime.prompts", "model_runtime.schema_guard"):
    sub = importlib.import_module(mod)
    out["submodules"][mod] = [n for n in sub.__all__ if not hasattr(sub, n)]
from model_runtime.wiring.schema_gate import schema   # 설치물에 schemas/가 들어 있다
from model_runtime.components.mimic_backend import load_rules   # 설치물에 data/mimic_rules.json이 들어 있다
out["schema_role"] = schema("Role")["title"]
out["mimic_rules"] = sorted(load_rules())[:1]
print(json.dumps(out))
'''


class WheelPublicNames(unittest.TestCase):
    def test_installed_wheel_exposes_every_public_name(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            src = t / "src_copy"   # 리포 안에서 빌드하면 이전 빌드의 build/lib가 섞인다. 깨끗한 복사본에서 빌드한다
            for name in ("pyproject.toml", "setup.py"):
                shutil.copy(ROOT / name, src / name) if src.mkdir(exist_ok=True) is None else None
            shutil.copytree(ROOT / "src", src / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            shutil.copytree(ROOT / "schemas", src / "schemas")
            subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", str(src), "-w", str(t / "whl")], check=True, capture_output=True)
            whl = next((t / "whl").glob("*.whl"))
            venv.create(t / "v", with_pip=True)
            py = t / "v" / "bin" / "python"
            subprocess.run([str(py), "-m", "pip", "install", "-q", "--no-deps", str(whl)], check=True, capture_output=True)
            deps = ":".join(p for p in site.getsitepackages())
            r = subprocess.run([str(py), "-c", PROBE], env={"PATH": "/usr/bin:/bin", "PYTHONPATH": deps}, capture_output=True, text=True, cwd=str(t))
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(out["file"].startswith(str(t / "v")), out["file"])   # 설치된 wheel을 읽었지 src 체크아웃이 아니다
            self.assertEqual(out["missing"], [])
            self.assertEqual(out["submodules"], {"model_runtime.prompts": [], "model_runtime.schema_guard": []})
            self.assertEqual(out["schema_role"], "Role")


if __name__ == "__main__":
    unittest.main()
