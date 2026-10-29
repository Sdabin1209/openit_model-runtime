"""scripts/setup-env.sh의 결과 한 줄(계약 v2.6.0 SetupEnvReport, OPEN_GAPS U43): 표준 출력 마지막 줄이 보고여야 하고 진행 기록은 표준 오류로 간다.
deploy는 environment.test_command만 읽고 이 영역의 .venv 경로나 시험 도구를 짐작하지 않는다(구조 규칙 1)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup-env.sh"


def last_report(stdout: str) -> dict:
    lines = [x for x in stdout.splitlines() if x.strip()]
    assert lines, "표준 출력이 비었다"
    return json.loads(lines[-1])


def test_failure_path_still_ends_with_a_valid_report(tmp_path):
    # Dockerfile에서 계약 태그를 읽지 못하면 실패(종료 2)한다. 네트워크 없이 확인되는 경로다.
    (tmp_path / "scripts").mkdir(); shutil.copy(SCRIPT, tmp_path / "scripts" / "setup-env.sh"); (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "setup-env.sh")], capture_output=True, text=True)
    assert r.returncode == 2
    rep = last_report(r.stdout)
    assert not list(validator("SetupEnvReport").iter_errors(rep)), rep
    assert (rep["area"], rep["status"], rep["exit_code"]) == ("model-runtime", "fail", 2)
    assert "environment" not in rep   # 준비하지 못한 환경은 선언하지 않는다
    assert len([x for x in r.stdout.splitlines() if x.strip()]) == 1   # 진행 기록은 표준 출력에 섞이지 않는다


def test_success_report_declares_environment_and_its_test_command_runs(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, cwd=tmp_path)   # 어느 폴더에서 불러도 된다
    rep = last_report(r.stdout)
    assert not list(validator("SetupEnvReport").iter_errors(rep)), rep
    if rep["status"] != "ok" and "재시도도 실패" in r.stderr:
        pytest.skip("계약 태그를 받지 못했다(네트워크·GitHub 접근 없음)")
    assert (r.returncode, rep["status"], rep["exit_code"]) == (0, "ok", 0), r.stderr[-400:]
    assert rep["area"] == "model-runtime" and isinstance(rep["seconds"], int)
    env = rep["environment"]
    assert env["kind"] == "python-venv" and env["path"] == ".venv" and (ROOT / env["path"] / "bin" / "python").exists()
    assert len([x for x in r.stdout.splitlines() if x.strip()]) == 1
    # 리포 루트에서 test_command(셸 없이 argv)를 실행하면 영역 시험이 돈다. 전체 시험은 이 시험 안에서 되풀이하지 않고 한 파일만 돌려 argv가 동작함을 본다.
    p = subprocess.run([*env["test_command"], "tests/test_contract_schemas.py"], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0 and "passed" in p.stdout, p.stdout[-400:]
