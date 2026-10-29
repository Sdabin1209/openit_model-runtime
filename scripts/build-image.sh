#!/usr/bin/env bash
# 영역 빌드 진입점(계약 boundary-contracts v2.5.0, BuildImageRequest/BuildImageResult, OPEN_GAPS U42, ADR 0025).
# 사용: bash scripts/build-image.sh <요청 JSON 파일>   (영역 리포 루트에서)
# 표준 출력의 마지막 줄만 BuildImageResult JSON 한 줄이다. 진행 기록은 표준 오류. 종료 0 성공.
# 환경 변수: DOCKER(없으면 docker), PYTHON(없으면 python3; jsonschema가 있어야 한다. 계약 패키지는 요청의 dependency_sources.boundary에서 읽는다).
# 라벨 정직성(U50): 라벨 값은 요청이 말하는 버전이 아니라 실제로 설치·사용하는 버전이다. 요청, 이 영역의 고정(Dockerfile ARG), 계약 소스 트리의 실제 버전이 다르면 이미지를 만들기 전에 실패하고,
# 만든 뒤에는 이미지 안에서 설치된 버전을 읽어 라벨과 대조한다(다르면 이미지를 지우고 실패).
# 왜 이 모양: deploy가 영역의 Dockerfile 인자 이름·빌드 컨텍스트·라벨 값을 알면 구조 규칙 1(영역은 서로의 구조를 모른다)을 어긴다. 그것은 이 영역이 정한다.
set -euo pipefail
REQ="${1:-}"; [ -n "$REQ" ] && [ -f "$REQ" ] || { echo "사용: bash scripts/build-image.sh <BuildImageRequest JSON 파일>" >&2; exit 2; }
DOCKER="${DOCKER:-docker}"; PY="${PYTHON:-python3}"
log() { echo "build-image: $*" >&2; }
die() { echo "build-image: $*" >&2; exit 1; }

# 1) 요청 검증과 필드 읽기. 계약 validator는 요청이 가리키는 계약 소스 트리의 패키지를 쓴다(빌드 중 네트워크·이웃 리포 경로 없음).
#    검증 전에는 그 경로 하나만 표준 json으로 읽는다. 오류에는 값 없이 경로와 키워드만 낸다.
BC_SRC="$("$PY" -I -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["dependency_sources"]["boundary"])' "$REQ" 2>/dev/null)" \
  || die "요청에 dependency_sources.boundary가 없다(이 영역은 계약 소스를 요청에서만 받는다)"
[ -d "$BC_SRC/boundary_contracts" ] || die "dependency_sources.boundary에 boundary_contracts 패키지가 없다"
FIELDS="$(PYTHONPATH="$BC_SRC" "$PY" -c '
import json, re, sys, tomllib
from boundary_contracts import validator
req = json.load(open(sys.argv[1]))
errs = sorted(validator("BuildImageRequest").iter_errors(req), key=lambda e: list(map(str, e.absolute_path)))
if errs:
    sys.exit("요청이 BuildImageRequest를 어긴다: " + ", ".join("/" + "/".join(map(str, e.absolute_path)) + ":" + e.validator for e in errs[:5]))
v, ds = req["versions"], req.get("dependency_sources", {})
need = [k for k in ("model_runtime", "survey_schema") if k not in v] + ["dependency_sources." + k for k in ("boundary", "survey_schema") if k not in ds]
if need:   # 이 영역이 쓰는 키: 이미지 라벨(model-runtime, survey-schema)과 빌드 컨텍스트(계약, 문진 스키마 내장). 기본값을 영역 안에 두지 않는다
    sys.exit("요청에 이 영역이 쓰는 키가 없다: " + ", ".join(need))
variant = req.get("variant", "-")   # "-"는 variant 없음(탭 구분 읽기에서 빈 필드가 사라지지 않게)
if variant not in ("-", "gpu"):
    sys.exit("모르는 variant: " + variant + " (이 영역은 기본(cpu)과 gpu)")
src = req["source_dir"]
def project_version(tree, what):
    try:
        return "v" + tomllib.load(open(tree + "/pyproject.toml", "rb"))["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        sys.exit(what + "의 pyproject.toml에서 버전을 읽지 못했다(" + tree + ")")
own = project_version(src, "source_dir")   # 라벨 io.openit.model-runtime은 이 소스의 버전이다
if v["model_runtime"] != own:
    sys.exit("라벨이 거짓이 된다: versions.model_runtime(" + v["model_runtime"] + ")이 source_dir의 pyproject version(" + own + ")과 다르다")
bc_actual = project_version(ds["boundary"], "dependency_sources.boundary")   # 이미지에 설치될 계약의 실제 버전
if v["boundary"] != bc_actual:
    sys.exit("라벨이 거짓이 된다: versions.boundary(" + v["boundary"] + ")가 dependency_sources.boundary 트리의 실제 버전(" + bc_actual + ")과 다르다")
pinned = re.search(r"^ARG BOUNDARY_VERSION=(v[0-9][0-9.]*)", open(src + "/Dockerfile", encoding="utf-8").read(), re.M)
if not pinned or pinned.group(1) != bc_actual:
    sys.exit("이 영역의 계약 고정(" + (pinned.group(1) if pinned else "읽지 못함") + ")과 요청의 계약 버전(" + bc_actual + ")이 다르다. 영역의 고정을 올린 뒤 다시 요청한다")
try:
    ss_actual = open(ds["survey_schema"] + "/CONTRACT_VERSION", encoding="utf-8").read().strip()
except OSError:
    sys.exit("dependency_sources.survey_schema에 CONTRACT_VERSION이 없다")
if ss_actual != v["survey_schema"].lstrip("v").split(".")[0]:
    sys.exit("라벨이 거짓이 된다: versions.survey_schema(" + v["survey_schema"] + ")의 주 버전이 문진 스키마 디렉터리의 CONTRACT_VERSION(" + ss_actual + ")과 다르다")
print("\t".join([src, req["revision"], bc_actual, own, ss_actual, ds["boundary"], ds["survey_schema"], variant, req["image_tag"]]))
' "$REQ")" || die "요청 검증 실패"
IFS=$'\t' read -r SRC REV BC_V MR_V SS_V BC_DIR SS_DIR VARIANT TAG <<< "$FIELDS"
[ "$VARIANT" != "-" ] || VARIANT=""
[ -f "$SRC/Dockerfile" ] || die "source_dir에 Dockerfile이 없다"
[ -d "$SS_DIR" ] || die "dependency_sources.survey_schema 디렉터리가 없다"
SS_MAJOR="$SS_V"   # 라벨 io.openit.contracts.survey-schema는 문진 스키마 디렉터리의 실제 CONTRACT_VERSION(주 버전)이다(ImageLabels)

# 2) 빌드. 같은 요청 -> 같은 이미지(인자와 컨텍스트가 요청에서만 온다). 문진 스키마는 이미지에 내장한다(D72).
ARGS=(--build-context "boundary=$BC_DIR" --build-context "schema=$SS_DIR"
      --build-arg "REVISION=$REV" --build-arg "BOUNDARY_VERSION=$BC_V" --build-arg "SURVEY_SCHEMA_VERSION=$SS_MAJOR"
      --build-arg "MODEL_RUNTIME_VERSION=$MR_V")
EXPECT_VARIANT=""
if [ "$VARIANT" = "gpu" ]; then ARGS+=(--build-arg VARIANT=gpu --label io.openit.variant=gpu); EXPECT_VARIANT=gpu; fi
log "docker build $TAG (variant=${VARIANT:-cpu})"
"$DOCKER" build -q -t "$TAG" "${ARGS[@]}" "$SRC" >&2 || die "docker build 실패"

# 2b) 라벨 정직성: 이미지 안에 실제로 설치된 버전을 읽어 라벨 값과 대조한다. 다르면 이미지를 지우고 실패한다(거짓 라벨이 남지 않게).
INSTALLED="$("$DOCKER" run --rm --entrypoint python "$TAG" -c "
import importlib.metadata as m, json, model_runtime
print(json.dumps({'boundary': 'v' + m.version('boundary-contracts'), 'model_runtime': 'v' + model_runtime.RUNTIME_VERSION,
                  'survey_schema': open('/opt/survey-schema/CONTRACT_VERSION').read().strip()}))")" || { "$DOCKER" rmi -f "$TAG" >&2 || true; die "이미지 안의 설치 버전을 읽지 못했다"; }
WANT_INSTALLED="$(printf '{"boundary": "%s", "model_runtime": "%s", "survey_schema": "%s"}' "$BC_V" "$MR_V" "$SS_MAJOR")"
"$PY" -c 'import json,sys;sys.exit(0 if json.loads(sys.argv[1]) == json.loads(sys.argv[2]) else 1)' "$INSTALLED" "$WANT_INSTALLED" \
  || { "$DOCKER" rmi -f "$TAG" >&2 || true; die "라벨이 거짓이 된다: 이미지에 실제로 설치된 버전 $INSTALLED 이(가) 라벨 값 $WANT_INSTALLED 과 다르다. 이미지를 지웠다"; }

# 3) 결과: 이미지에 실제로 붙은 라벨과 ID를 읽는다. 요청 값과 같아야 하고 계약 ImageLabels·BuildImageResult를 통과해야 한다.
IMAGE_ID="$("$DOCKER" image inspect "$TAG" --format '{{.Id}}')"
LABELS_JSON="$("$DOCKER" image inspect "$TAG" --format '{{json .Config.Labels}}')"
PYTHONPATH="$BC_SRC" "$PY" -c '
import json, sys
from boundary_contracts import validator
tag, image_id, labels_json, rev, bc, mr, ss, variant = sys.argv[1:9]
labels = {k: v for k, v in json.loads(labels_json).items() if k.startswith("io.openit.")}
want = {"io.openit.service": "model-runtime", "io.openit.revision": rev, "io.openit.contracts.boundary": bc, "io.openit.model-runtime": mr,
        "io.openit.contracts.survey-schema": ss}
if variant:
    want["io.openit.variant"] = variant
bad = sorted(k for k in want if labels.get(k) != want[k])
if bad:
    sys.exit("이미지 라벨이 요청과 다르다: " + ", ".join(bad))
result = {"image": tag, "image_id": image_id, "labels": labels}
errs = list(validator("BuildImageResult").iter_errors(result))
if errs:
    sys.exit("결과가 BuildImageResult를 어긴다: " + ", ".join("/" + "/".join(map(str, e.absolute_path)) + ":" + e.validator for e in errs[:5]))
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
' "$TAG" "$IMAGE_ID" "$LABELS_JSON" "$REV" "$BC_V" "$MR_V" "$SS_MAJOR" "$EXPECT_VARIANT"
