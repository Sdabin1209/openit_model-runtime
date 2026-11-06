"""이미지 라벨이 계약 v2.2.0 이후의 ImageLabels(D108)를 지키는지. Docker 없이 Dockerfile의 ARG 기본값·LABEL과
scripts/build-image.sh가 넘기는 빌드 인자 이름을 읽어 라벨 값을 만들고 계약 검증기로 본다. 실제 이미지 라벨은 smoke.sh가 docker image inspect로 본다."""
import re
import tomllib
import unittest
from pathlib import Path

from boundary_contracts import validator

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
ENTRY = (ROOT / "scripts" / "build-image.sh").read_text(encoding="utf-8")


def arg_defaults() -> dict:
    out = {}
    for m in re.finditer(r"^ARG (\w+)(?:=(\S*))?", DOCKERFILE, re.M):
        out[m.group(1)] = m.group(2)   # 기본값 없으면 None
    return out


def label_template() -> dict:
    block = re.search(r"^LABEL (.*?)(?=^\S)", DOCKERFILE, re.M | re.S).group(1)
    return dict(re.findall(r'(io\.openit\.[\w.-]+)="([^"]*)"', block))


def render(args: dict) -> dict:
    return {k: re.sub(r"\$\{(\w+)\}", lambda m: args[m.group(1)] or "", v) for k, v in label_template().items()}


def entry_args() -> dict:
    """영역 빌드 진입점(scripts/build-image.sh, ADR 0025)이 넘기는 --build-arg 이름과, 요청 값으로 채운 예시 값. 이름이 Dockerfile의 ARG에 없으면 시험이 실패한다."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    values = {"SERVICE_NAME": "model-serving", "REVISION": "0123abc", "BOUNDARY_VERSION": arg_defaults()["BOUNDARY_VERSION"], "SURVEY_SCHEMA_VERSION": "1", "MODEL_RUNTIME_VERSION": f"v{version}"}
    names = re.findall(r'--build-arg "(\w+)=', ENTRY)
    unknown = [n for n in names if n not in arg_defaults()]
    assert not unknown, f"build-image.sh가 Dockerfile에 없는 ARG를 넘긴다: {unknown}"
    return {n: values[n] for n in names}


def entry_labels() -> dict:
    """빌드 진입점이 --label 로 직접 다는 선언 라벨(값은 리포의 정본 파일): 노출(io.openit.exposure)과 주입(io.openit.inputs, 계약 3.5.0 필수).
    Dockerfile 만으로 빌드하면 주입 선언이 없다: 계약을 지키는 이미지는 진입점(build-image.sh)으로만 만든다."""
    import json
    names = re.findall(r'--label "(io\.openit\.[\w.-]+)=\$', ENTRY)
    src = {"io.openit.exposure": ("deploy-exposure.json", "service"), "io.openit.inputs": ("deploy-inputs.json", "image")}
    assert set(names) <= set(src), f"build-image.sh 가 모르는 라벨을 단다: {sorted(set(names) - set(src))}"
    out = {}
    for n in names:
        doc = json.loads((ROOT / src[n][0]).read_text(encoding="utf-8")); doc[src[n][1]] = "model-serving"
        out[n] = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return out


class ImageLabels(unittest.TestCase):
    def errors(self, labels):
        return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator("ImageLabels").iter_errors(labels)]

    def test_entrypoint_build_labels_satisfy_contract(self):
        labels = {**render({**arg_defaults(), **entry_args()}), **entry_labels()}
        self.assertEqual(self.errors(labels), [], labels)
        self.assertIn("io.openit.inputs", labels)
        self.assertRegex(labels["io.openit.model-runtime"], r"^v\d+\.\d+\.\d+$")

    def test_defaults_for_optional_args_are_contract_values(self):
        # 빌드 인자를 주지 않아도 되는 라벨(survey-schema)의 기본값이 계약 값이어야 한다(none·unknown 불가, n/a)
        args = {**arg_defaults(), "SERVICE_NAME": "model-serving", "REVISION": "0123abc", "MODEL_RUNTIME_VERSION": "v0.0.0"}
        self.assertEqual(self.errors({**render(args), **entry_labels()}), [])


if __name__ == "__main__":
    unittest.main()
