"""영역 빌드 진입점 scripts/build-image.sh (계약 v2.5.0 BuildImageRequest/BuildImageResult, ADR 0025).
도커는 DOCKER 환경 변수로 바꿔 끼운 가짜 실행 파일로 대신해 인자·라벨·결과 모양을 본다(실제 빌드는 scripts/smoke.sh가 한다)."""
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import boundary_contracts
import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-image.sh"
PINNED = re.search(r"^ARG BOUNDARY_VERSION=(v[0-9][0-9.]*)", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M).group(1)   # 이 영역의 계약 고정(정본)
VERSION = "v" + tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

STUB = '''#!{py}
import json, os, sys
log = os.environ["STUB_LOG"]
a = sys.argv[1:]
if a[0] == "build":
    json.dump(a, open(log, "w"))
elif a[:2] == ["image", "inspect"]:
    b = json.load(open(log))
    args = {{b[i + 1].split("=", 1)[0]: b[i + 1].split("=", 1)[1] for i, x in enumerate(b) if x == "--build-arg"}}
    labels = {{"io.openit.service": "model-runtime", "io.openit.revision": args["REVISION"], "io.openit.contracts.boundary": args["BOUNDARY_VERSION"],
              "io.openit.contracts.survey-schema": args["SURVEY_SCHEMA_VERSION"], "io.openit.model-runtime": args["MODEL_RUNTIME_VERSION"], "org.opencontainers.image.x": "ignored"}}
    for i, x in enumerate(b):
        if x == "--label":
            k, v = b[i + 1].split("=", 1); labels[k] = v
    if os.environ.get("STUB_BAD_LABEL"):
        labels["io.openit.revision"] = "ffffff0"
    print("sha256:" + "ab" * 32 if "{{.Id}}" in a[-1] else json.dumps(labels))
elif a[0] == "run":   # 이미지 안에 실제로 설치된 버전(라벨 정직성 확인용)
    b = json.load(open(log))
    args = {{b[i + 1].split("=", 1)[0]: b[i + 1].split("=", 1)[1] for i, x in enumerate(b) if x == "--build-arg"}}
    got = {{"boundary": args["BOUNDARY_VERSION"], "model_runtime": args["MODEL_RUNTIME_VERSION"], "survey_schema": args["SURVEY_SCHEMA_VERSION"]}}
    if os.environ.get("STUB_INSTALLED_BOUNDARY"):
        got["boundary"] = os.environ["STUB_INSTALLED_BOUNDARY"]
    print(json.dumps(got))
elif a[:2] == ["rmi", "-f"]:
    open(log + ".rmi", "w").write(a[2])
else:
    sys.exit("unexpected " + " ".join(a))
'''


@pytest.fixture
def env(tmp_path):
    stub = tmp_path / "docker"
    stub.write_text(STUB.format(py=sys.executable)); stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    ss = tmp_path / "schema"; ss.mkdir(); (ss / "CONTRACT_VERSION").write_text("1\n")   # 정수 계약 번호(모델 런타임 호환). 라벨과 다르다
    (ss / "pyproject.toml").write_text('[project]\nname = "openit-survey-schema"\nversion = "2.0.0"\n')   # 문진 스키마 패키지 버전: 라벨의 정본
    bc = tmp_path / "bc"; bc.mkdir(); (bc / "boundary_contracts").symlink_to(Path(boundary_contracts.__file__).resolve().parent)   # 계약 소스 트리 모양: 패키지 + pyproject
    (bc / "pyproject.toml").write_text(f'[project]\nname = "boundary-contracts"\nversion = "{PINNED[1:]}"\n')
    return {"stub": stub, "log": tmp_path / "log.json", "ss": ss, "bc": bc, "tmp": tmp_path}


def request(env, **over):
    req = {"source_dir": str(ROOT), "revision": "abc1234", "versions": {"boundary": PINNED, "model_runtime": VERSION, "survey_schema": "v2.0.0"},
           "dependency_sources": {"boundary": str(env["bc"]), "survey_schema": str(env["ss"])}, "image_tag": "model-runtime-test/model-serving:abc1234"}
    for k, v in over.items():
        req[k] = v
    return req


def run(env, req, **extra_env):
    f = env["tmp"] / "req.json"; f.write_text(json.dumps(req))
    return subprocess.run(["bash", str(SCRIPT), str(f)], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, "DOCKER": str(env["stub"]), "STUB_LOG": str(env["log"]), "PYTHON": sys.executable, **extra_env})


def test_success_last_line_is_build_image_result_and_labels_match_request(env):
    r = run(env, request(env))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert not list(validator("BuildImageResult").iter_errors(out))
    assert out["image"] == "model-runtime-test/model-serving:abc1234" and out["image_id"].startswith("sha256:")
    assert out["labels"] == {"io.openit.service": "model-runtime", "io.openit.revision": "abc1234", "io.openit.contracts.boundary": PINNED,
                             "io.openit.contracts.survey-schema": "2", "io.openit.model-runtime": VERSION}
    assert json.loads(env["log"].read_text())[0] == "build"


def test_gpu_variant_passes_variant_arg_and_label(env):
    r = run(env, request(env, variant="gpu"))
    assert r.returncode == 0, r.stderr
    build = json.loads(env["log"].read_text())
    assert "VARIANT=gpu" in build and "io.openit.variant=gpu" in build
    assert json.loads(r.stdout.strip().splitlines()[-1])["labels"]["io.openit.variant"] == "gpu"


def test_build_context_and_args_come_only_from_the_request(env):
    run(env, request(env))
    build = json.loads(env["log"].read_text())
    assert f"boundary={env['bc']}" in build and f"schema={env['ss']}" in build and build[-1] == str(ROOT)
    assert "model-runtime-test/model-serving:abc1234" in build


@pytest.mark.parametrize("mutate,why", [
    (lambda q: q.update(unknown_key=1), "모르는 키"),
    (lambda q: q.pop("revision"), "revision 없음"),
    (lambda q: q["versions"].pop("boundary"), "versions.boundary 없음"),
    (lambda q: q["versions"].pop("model_runtime"), "이 영역이 쓰는 versions.model_runtime 없음"),
    (lambda q: q["versions"].pop("survey_schema"), "이 영역이 쓰는 versions.survey_schema 없음"),
    (lambda q: q["dependency_sources"].pop("survey_schema"), "이 영역이 쓰는 dependency_sources.survey_schema 없음"),
    (lambda q: q["versions"].update(boundary="3.0.0"), "버전 형식"),
    (lambda q: q["versions"].update(turn_engine="v0.4.3"), "3.0.0에서 빼진 versions.turn_engine"),
    (lambda q: q.update(variant="tpu"), "모르는 variant"),
    (lambda q: q["versions"].update(model_runtime="v9.9.9"), "소스 버전과 다른 model_runtime"),
])
def test_bad_requests_fail_before_docker(env, mutate, why):
    q = request(env); mutate(q)
    r = run(env, q)
    assert r.returncode != 0, why
    assert not env["log"].exists(), why   # 도커를 부르지 않았다


def test_missing_boundary_source_fails(env):
    q = request(env); q["dependency_sources"].pop("boundary")
    assert run(env, q).returncode != 0


def test_label_mismatch_fails(env):
    r = run(env, request(env), STUB_BAD_LABEL="1")
    assert r.returncode != 0 and "라벨" in r.stderr


# ---- U50 라벨 정직성: 라벨은 요청이 말하는 버전이 아니라 실제로 설치·사용하는 버전이다 ----
def test_request_boundary_differing_from_our_pin_fails_before_docker(env):
    other = "v9.9.9"
    q = request(env); q["versions"]["boundary"] = other
    (env["bc"] / "pyproject.toml").write_text('[project]\nname = "boundary-contracts"\nversion = "9.9.9"\n')   # 트리도 요청과 같다: 요청+트리는 일치하지만 이 영역의 고정과 다르다
    r = run(env, q)
    assert r.returncode != 0 and "계약 고정" in r.stderr and PINNED in r.stderr and other in r.stderr, r.stderr
    assert not env["log"].exists()


def test_request_boundary_differing_from_the_source_tree_fails_before_docker(env):
    q = request(env); q["versions"]["boundary"] = "v9.9.9"   # 트리의 실제 버전은 PINNED
    r = run(env, q)
    assert r.returncode != 0 and "라벨이 거짓" in r.stderr and "실제 버전" in r.stderr, r.stderr
    assert not env["log"].exists()


def test_survey_schema_label_is_the_package_major_not_the_integer_contract_version(env):
    r = run(env, request(env))   # 패키지 2.0.0, CONTRACT_VERSION 1: 라벨은 2다
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["labels"]["io.openit.contracts.survey-schema"] == "2"
    assert "SURVEY_SCHEMA_VERSION=2" in json.loads(env["log"].read_text())


def test_survey_schema_major_differing_from_the_package_version_fails_before_docker(env):
    q = request(env); q["versions"]["survey_schema"] = "v3.0.0"   # 패키지 버전은 2.0.0
    r = run(env, q)
    assert r.returncode != 0 and "라벨이 거짓" in r.stderr and "문진 스키마 패키지" in r.stderr, r.stderr
    assert not env["log"].exists()


def test_survey_schema_dir_without_package_version_fails(env):
    (env["ss"] / "pyproject.toml").unlink()
    r = run(env, request(env))
    assert r.returncode != 0 and "pyproject.toml" in r.stderr, r.stderr
    assert not env["log"].exists()


def test_labels_come_from_the_actual_sources_not_the_request_text(env):
    r = run(env, request(env))
    assert r.returncode == 0, r.stderr
    build = json.loads(env["log"].read_text())
    assert f"BOUNDARY_VERSION={PINNED}" in build and "SURVEY_SCHEMA_VERSION=2" in build and f"MODEL_RUNTIME_VERSION={VERSION}" in build


def test_installed_version_in_the_image_differing_from_the_label_removes_image_and_fails(env):
    r = run(env, request(env), STUB_INSTALLED_BOUNDARY="v0.0.1")   # 이미지 안에 실제로 설치된 계약이 라벨과 다르다
    assert r.returncode != 0 and "라벨이 거짓" in r.stderr and "v0.0.1" in r.stderr, r.stderr
    assert (env["log"].with_name("log.json.rmi")).read_text() == "model-runtime-test/model-serving:abc1234"   # 거짓 라벨 이미지를 남기지 않는다
    assert not r.stdout.strip()   # 결과 한 줄도 내지 않는다
