#!/usr/bin/env bash
# 컨테이너 스모크(CPU/가짜 백엔드): 이미지를 빌드해 /healthz, /readyz, /v1/.../extract, 라벨, non-root를 확인한다.
# 고정 포트를 쓰지 않는다(Docker가 호스트 포트를 배정). 사용: scripts/smoke.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
# 계약 태그의 정본은 Dockerfile의 ARG BOUNDARY_VERSION이다(여기에 버전 숫자를 따로 적지 않는다). BOUNDARY_TAG 환경 변수로 덮어쓸 수 있다
BOUNDARY_TAG="${BOUNDARY_TAG:-$(sed -n 's/^ARG BOUNDARY_VERSION=\(v[0-9][0-9.]*\).*/\1/p' "$HERE/Dockerfile" | head -1)}"
# 기대 라벨 값의 정본은 pyproject.toml의 version이다(RUNTIME_VERSION과 같아야 하며 tests가 확인한다)
MR_VERSION="$(python3 -c 'import sys,tomllib;print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$HERE/pyproject.toml")"
TAG="model-runtime:smoke-$$"; NAME="model-runtime-smoke-$$"
BDIR="$(mktemp -d)"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true; rm -rf "$BDIR"' EXIT
# 계약 소스는 작업 트리가 아니라 태그 그대로다. 형제 폴더가 있으면 거기서 git archive로(오프라인 가능), 없으면 GitHub에서 태그를 얕게 clone한다(새 머신은 형제 폴더가 없다).
# BOUNDARY_REPO: 형제 폴더 배치가 다른 작업 트리(git worktree)에서 계약 리포 위치를 줄 때 쓴다
if git -C "${BOUNDARY_REPO:-$HERE/../boundary-contracts}" archive "$BOUNDARY_TAG" 2>/dev/null | tar -x -C "$BDIR" 2>/dev/null && [ -f "$BDIR/pyproject.toml" ]; then :
else
  rm -rf "$BDIR"; BDIR="$(mktemp -d)"
  git clone -q --depth 1 --branch "$BOUNDARY_TAG" git@github.com:Sdabin1209/openit_boundary-contracts.git "$BDIR" && rm -rf "$BDIR/.git" \
    || { echo "boundary-contracts 태그 $BOUNDARY_TAG를 형제 폴더에서도 GitHub에서도 꺼내지 못했다" >&2; exit 1; }
fi
# 이미지는 영역 빌드 진입점으로 만든다(deploy가 부르는 것과 같은 경로, ADR 0025). 문진 스키마는 시험 고정물이고 버전은 그 pyproject.toml의 패키지 버전이다
SSV="v$(python3 -c 'import sys,tomllib;print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$HERE/tests/fixtures/schema/pyproject.toml")"
REQDIR="$(mktemp -d)"; trap 'docker rm -f "$NAME" "${NAME}-ro" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true; rm -rf "$BDIR" "$REQDIR"' EXIT   # 요청 파일은 계약 소스(빌드 컨텍스트) 밖에 둔다
REQ="$REQDIR/request.json"
python3 -c 'import json,sys;a=sys.argv;print(json.dumps({"source_dir":a[1],"revision":a[2],"versions":{"boundary":a[3],"model_runtime":a[4],"survey_schema":a[5]},
  "dependency_sources":{"boundary":a[6],"survey_schema":a[7]},"image_tag":a[8]}))' "$HERE" "$(git -C "$HERE" rev-parse --short HEAD)" "$BOUNDARY_TAG" "v$MR_VERSION" "$SSV" "$BDIR" "$HERE/tests/fixtures/schema" "$TAG" > "$REQ"
PYTHON="${PYTHON:-$( [ -x "$HERE/.venv/bin/python" ] && echo "$HERE/.venv/bin/python" || echo python3)}" bash "$HERE/scripts/build-image.sh" "$REQ" | tail -1 | tee "$REQDIR/result.json"
docker run -d --name "$NAME" -p 127.0.0.1::8080 -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_ALLOW_FAKE=1 -e MODEL_RUNTIME_ALLOW_NO_AUTH=1 -e MODEL_RUNTIME_RELEASE_URL=http://127.0.0.1:9 -e MODEL_RUNTIME_PUT_WAIT_S=5 \
  "$TAG" >/dev/null
PORT="$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')"; URL="http://127.0.0.1:$PORT"
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null && break; sleep 1; done
curl -sf "$URL/healthz"; echo
# I4(release-gate)가 없으므로 /readyz는 503과 사유여야 한다(/healthz는 200)
code="$(curl -s -o /tmp/rz.$$ -w '%{http_code}' "$URL/readyz")"; cat /tmp/rz.$$; echo; rm -f /tmp/rz.$$
[ "$code" = 503 ] || { echo "I4에 닿지 못하면 readyz 503이어야 한다: $code" >&2; exit 1; }
curl -sf "$URL/v1/info"; echo
curl -sf "$URL/v1/releases/smoke-1"; echo
# 본문은 계약(ResponseRequest)을 지키는 것이어야 404까지 닿는다(통신 기반 라이브러리가 본문 계약 검증을 경로 처리보다 먼저 한다: 틀린 본문은 400)
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/v1/releases/smoke-1/respond" -H 'Content-Type: application/json' -d '{"action":"START","topic":"뇌졸중 진단과 약","next":{"type":"NEXT","question_id":"Q1.D01","text":"혹시 뇌졸중으로 진단받으셨나요?"}}')"
[ "$code" = 404 ] || { echo "적재 안 된 릴리스는 404여야 한다: $code" >&2; exit 1; }
# I4 서버가 없으므로 적재는 UNAVAILABLE(503)로 끝나야 한다(경로와 오류 형식 확인). 전체 한 바퀴는 tests/test_server_e2e.py와 gpu_check.sh serve가 확인한다.
code="$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$URL/v1/releases/smoke-1")"
echo "PUT without I4: $code"; [ "$code" = 503 ] || { echo "503이어야 한다" >&2; exit 1; }
# 스키마는 이미지에 들어 있다(마운트 없음). 새 볼륨도 번들 디렉터리에 쓸 수 있어야 한다. 쓸 수 없으면 /readyz 503과 이유.
docker exec "$NAME" python -c "import os,tempfile;tempfile.TemporaryFile(dir=os.environ['MODEL_RUNTIME_BUNDLE_DIR'])" && echo "bundle dir writable"
NAME2="$NAME-ro"
docker run -d --name "$NAME2" -p 127.0.0.1::8080 -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_ALLOW_FAKE=1 -e MODEL_RUNTIME_ALLOW_NO_AUTH=1 --tmpfs /var/lib/model-runtime/bundles:ro "$TAG" >/dev/null
trap 'docker rm -f "$NAME" "$NAME2" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true; rm -rf "$BDIR" "$REQDIR"' EXIT
PORT2="$(docker port "$NAME2" 8080/tcp | head -1 | sed 's/.*://')"
for i in $(seq 1 20); do curl -sf "http://127.0.0.1:$PORT2/healthz" >/dev/null && break; sleep 1; done
code="$(curl -s -o /tmp/ro.$$ -w '%{http_code}' "http://127.0.0.1:$PORT2/readyz")"; cat /tmp/ro.$$; echo; rm -f /tmp/ro.$$
[ "$code" = 503 ] || { echo "쓸 수 없는 번들 디렉터리면 readyz 503이어야 한다: $code" >&2; exit 1; }
echo "user=$(docker exec "$NAME" id -u)"; [ "$(docker exec "$NAME" id -u)" != 0 ] || { echo "root로 실행됨" >&2; exit 1; }
LABELS="$(docker image inspect "$TAG" --format '{{json .Config.Labels}}')"; echo "$LABELS"
echo "$LABELS" | grep -q "\"io.openit.contracts.boundary\":\"$BOUNDARY_TAG\"" || { echo "라벨의 계약 버전이 $BOUNDARY_TAG가 아니다" >&2; exit 1; }
# 기대값은 서로 독립인 두 출처다: (1) pyproject의 version(빌드 인자로 넘긴 값과 같은 파일이지만 라벨이 실제로 채워졌는지 본다) (2) 이미지에 설치된 패키지의 RUNTIME_VERSION
INST_VERSION="$(docker exec "$NAME" python -c 'import model_runtime as m;print(m.RUNTIME_VERSION)')"
[ "$INST_VERSION" = "$MR_VERSION" ] || { echo "설치된 RUNTIME_VERSION($INST_VERSION)이 pyproject version($MR_VERSION)과 다르다" >&2; exit 1; }
echo "$LABELS" | grep -q "\"io.openit.model-runtime\":\"v$INST_VERSION\"" || { echo "라벨의 model-runtime 버전이 설치된 RUNTIME_VERSION($INST_VERSION)과 다르다" >&2; exit 1; }
# 기본은 닫힘(MR-1): 토큰 없이는, 그리고 ALLOW_FAKE 없이 fake로는 시작하지 않는다.
! docker run --rm -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_ALLOW_FAKE=1 "$TAG" >/dev/null 2>&1 || { echo "토큰 없이 시작되면 안 된다" >&2; exit 1; }
! docker run --rm -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_TOKENS=survey=smoke-token "$TAG" >/dev/null 2>&1 || { echo "ALLOW_FAKE 없이 fake로 시작되면 안 된다" >&2; exit 1; }
echo "스모크 통과"
