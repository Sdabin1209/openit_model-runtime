"""영역 빌드 진입점 scripts/build-image.sh (계약 v2.5.0 BuildImageRequest/BuildImageResult, ADR 0025).
도커는 DOCKER 환경 변수로 바꿔 끼운 가짜 실행 파일로 대신해 인자·라벨·결과 모양을 본다(실제 빌드는 scripts/smoke.sh가 한다)."""
import json
import os
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
BC_SRC = Path(boundary_contracts.__file__).resolve().parent.parent   # 계약 소스 트리 대신 설치된 패키지의 부모(boundary_contracts/ 디렉터리를 가진다)
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
              "io.openit.contracts.survey-schema": args["SURVEY_SCHEMA_VERSION"], "io.openit.turn-engine": args["TURN_ENGINE_VERSION"],
              "io.openit.model-runtime": args["MODEL_RUNTIME_VERSION"], "org.opencontainers.image.x": "ignored"}}
    for i, x in enumerate(b):
        if x == "--label":
            k, v = b[i + 1].split("=", 1); labels[k] = v
    if os.environ.get("STUB_BAD_LABEL"):
        labels["io.openit.revision"] = "ffffff0"
    print("sha256:" + "ab" * 32 if "{{.Id}}" in a[-1] else json.dumps(labels))
else:
    sys.exit("unexpected " + " ".join(a))
'''


@pytest.fixture
def env(tmp_path):
    stub = tmp_path / "docker"
    stub.write_text(STUB.format(py=sys.executable)); stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    ss = tmp_path / "schema"; ss.mkdir()
    return {"stub": stub, "log": tmp_path / "log.json", "ss": ss, "tmp": tmp_path}


def request(env, **over):
    req = {"source_dir": str(ROOT), "revision": "abc1234", "versions": {"boundary": "v2.5.1", "model_runtime": VERSION, "survey_schema": "v1.1.0", "turn_engine": "v0.4.3"},
           "dependency_sources": {"boundary": str(BC_SRC), "survey_schema": str(env["ss"])}, "image_tag": "model-runtime-test/model-serving:abc1234"}
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
    assert out["labels"] == {"io.openit.service": "model-runtime", "io.openit.revision": "abc1234", "io.openit.contracts.boundary": "v2.5.1",
                             "io.openit.contracts.survey-schema": "1", "io.openit.turn-engine": "n/a", "io.openit.model-runtime": VERSION}
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
    assert f"boundary={BC_SRC}" in build and f"schema={env['ss']}" in build and build[-1] == str(ROOT)
    assert "model-runtime-test/model-serving:abc1234" in build


@pytest.mark.parametrize("mutate,why", [
    (lambda q: q.update(unknown_key=1), "모르는 키"),
    (lambda q: q.pop("revision"), "revision 없음"),
    (lambda q: q["versions"].pop("boundary"), "versions.boundary 없음"),
    (lambda q: q["versions"].pop("model_runtime"), "이 영역이 쓰는 versions.model_runtime 없음"),
    (lambda q: q["versions"].pop("survey_schema"), "이 영역이 쓰는 versions.survey_schema 없음"),
    (lambda q: q["dependency_sources"].pop("survey_schema"), "이 영역이 쓰는 dependency_sources.survey_schema 없음"),
    (lambda q: q["versions"].update(boundary="2.5.1"), "버전 형식"),
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
