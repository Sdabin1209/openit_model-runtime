#!/usr/bin/env bash
# 컨테이너 스모크(CPU/가짜 백엔드): 이미지를 빌드해 /healthz, /readyz, /v1/parse, /v1/generate, 라벨, non-root를 확인한다.
# 고정 포트를 쓰지 않는다(Docker가 호스트 포트를 배정). 사용: scripts/smoke.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TAG="model-runtime:smoke-$$"; NAME="model-runtime-smoke-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
docker build -q --build-context boundary="$HERE/../boundary-contracts" --build-arg REVISION="$(git -C "$HERE" rev-parse --short HEAD)" \
  --build-arg BOUNDARY_VERSION="$(git -C "$HERE/../boundary-contracts" describe --tags 2>/dev/null || echo unknown)" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat "$HERE/tests/fixtures/schema/CONTRACT_VERSION")" -t "$TAG" "$HERE" >/dev/null
docker run -d --name "$NAME" -p 127.0.0.1::8080 -e MODEL_RUNTIME_BACKEND=fake -e MODEL_RUNTIME_SCHEMA_DIR=/schema/schemas \
  -v "$HERE/tests/fixtures/schema:/schema:ro" "$TAG" >/dev/null
PORT="$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')"; URL="http://127.0.0.1:$PORT"
for i in $(seq 1 30); do curl -sf "$URL/healthz" >/dev/null && break; sleep 1; done
curl -sf "$URL/healthz"; echo; curl -sf "$URL/readyz"; echo
curl -sf -X POST "$URL/v1/parse" -H 'Content-Type: application/json' \
  -d '{"utterance":"머리가 아파요","last_directive":null,"awaiting_questions":[],"question_names":[],"answered_slots":[],"guide_list":[]}'; echo
echo "user=$(docker exec "$NAME" id -u)"; [ "$(docker exec "$NAME" id -u)" != 0 ] || { echo "root로 실행됨" >&2; exit 1; }
docker image inspect "$TAG" --format '{{json .Config.Labels}}'
echo "스모크 통과"
