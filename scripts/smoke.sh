#!/usr/bin/env bash
# 컨테이너 스모크(CPU/가짜 백엔드): 이미지를 빌드해 /healthz, /readyz, /v1/.../extract, 라벨, non-root를 확인한다.
# 고정 포트를 쓰지 않는다(Docker가 호스트 포트를 배정). 사용: scripts/smoke.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BOUNDARY_TAG="${BOUNDARY_TAG:-v2.0.0}"   # 계약 태그(공통 규칙). 형제 폴더가 이 태그가 아니면 중단한다
TAG="model-runtime:smoke-$$"; NAME="model-runtime-smoke-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
[ "$(git -C "$HERE/../boundary-contracts" describe --tags --exact-match 2>/dev/null || true)" = "$BOUNDARY_TAG" ] || { echo "boundary-contracts 형제 폴더가 태그 $BOUNDARY_TAG가 아니다" >&2; exit 1; }
docker build -q --build-context boundary="$HERE/../boundary-contracts" --build-context schema="$HERE/tests/fixtures/schema" --build-arg REVISION="$(git -C "$HERE" rev-parse --short HEAD)" \
  --build-arg BOUNDARY_VERSION="$BOUNDARY_TAG" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat "$HERE/tests/fixtures/schema/CONTRACT_VERSION")" -t "$TAG" "$HERE" >/dev/null
docker run -d --name "$NAME" -p 127.0.0.1::8080 -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_RELEASE_URL=http://127.0.0.1:9 -e MODEL_RUNTIME_PUT_WAIT_S=5 \
  "$TAG" >/dev/null
PORT="$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')"; URL="http://127.0.0.1:$PORT"
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null && break; sleep 1; done
curl -sf "$URL/healthz"; echo
# I4(release-gate)가 없으므로 /readyz는 503과 사유여야 한다(/healthz는 200)
code="$(curl -s -o /tmp/rz.$$ -w '%{http_code}' "$URL/readyz")"; cat /tmp/rz.$$; echo; rm -f /tmp/rz.$$
[ "$code" = 503 ] || { echo "I4에 닿지 못하면 readyz 503이어야 한다: $code" >&2; exit 1; }
curl -sf "$URL/v1/info"; echo
curl -sf "$URL/v1/releases/smoke-1"; echo
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/v1/releases/smoke-1/extract" -H 'Content-Type: application/json' -d '{}')"
[ "$code" = 404 ] || { echo "적재 안 된 릴리스는 404여야 한다: $code" >&2; exit 1; }
# I4 서버가 없으므로 적재는 UNAVAILABLE(503)로 끝나야 한다(경로와 오류 형식 확인). 전체 한 바퀴는 tests/test_server_e2e.py와 gpu_check.sh serve가 확인한다.
code="$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$URL/v1/releases/smoke-1")"
echo "PUT without I4: $code"; [ "$code" = 503 ] || { echo "503이어야 한다" >&2; exit 1; }
# 스키마는 이미지에 들어 있다(마운트 없음). 새 볼륨도 번들 디렉터리에 쓸 수 있어야 한다. 쓸 수 없으면 /readyz 503과 이유.
docker exec "$NAME" python -c "import os,tempfile;tempfile.TemporaryFile(dir=os.environ['MODEL_RUNTIME_BUNDLE_DIR'])" && echo "bundle dir writable"
NAME2="$NAME-ro"
docker run -d --name "$NAME2" -p 127.0.0.1::8080 -e MODEL_RUNTIME_BACKEND=fake --tmpfs /var/lib/model-runtime/bundles:ro "$TAG" >/dev/null
trap 'docker rm -f "$NAME" "$NAME2" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
PORT2="$(docker port "$NAME2" 8080/tcp | head -1 | sed 's/.*://')"
for i in $(seq 1 20); do curl -sf "http://127.0.0.1:$PORT2/healthz" >/dev/null && break; sleep 1; done
code="$(curl -s -o /tmp/ro.$$ -w '%{http_code}' "http://127.0.0.1:$PORT2/readyz")"; cat /tmp/ro.$$; echo; rm -f /tmp/ro.$$
[ "$code" = 503 ] || { echo "쓸 수 없는 번들 디렉터리면 readyz 503이어야 한다: $code" >&2; exit 1; }
echo "user=$(docker exec "$NAME" id -u)"; [ "$(docker exec "$NAME" id -u)" != 0 ] || { echo "root로 실행됨" >&2; exit 1; }
LABELS="$(docker image inspect "$TAG" --format '{{json .Config.Labels}}')"; echo "$LABELS"
echo "$LABELS" | grep -q "\"io.openit.contracts.boundary\":\"$BOUNDARY_TAG\"" || { echo "라벨의 계약 버전이 $BOUNDARY_TAG가 아니다" >&2; exit 1; }
echo "$LABELS" | grep -q '"io.openit.model-runtime":"0.6.3"' || { echo "라벨의 model-runtime 버전이 0.6.3가 아니다" >&2; exit 1; }
echo "스모크 통과"
