#!/usr/bin/env bash
# [PC에서] 서버로 올릴 키트 tar.gz를 만든다. 서버는 인터넷/GitHub 없이 이것만 풀어 PYTHONPATH로 쓴다(ADR 0013).
# 내용: mr-kit/src(model_runtime), mr-kit/pylib(boundary_contracts: 설치된 태그 패키지 그대로, 스키마 포함), mr-kit/survey-schema(문진 스키마 schemas/, CONTRACT_VERSION),
#       mr-kit/profiles, mr-kit/scripts/gpu_session, mr-kit/KIT_MANIFEST.txt(커밋·boundary 버전·파일 해시). 데이터(C1)와 어댑터는 넣지 않는다.
# 사용: make_kit.sh OUT.tar.gz [--python PY] [--schema-root DIR(survey-service/schema, 주면 그 폴더를 그대로 넣는다)]   기본: 문진 스키마 태그 SURVEY_SCHEMA_TAG(기본 survey-schema-v4.1.1)
# 서버 쪽: mkdir -p ~/dabin && tar -xzf OUT.tar.gz -C ~/dabin   (-> ~/dabin/mr-kit)
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?출력 tar.gz 경로}"; shift || true
# 키트에 넣는 문진 스키마의 판(태그). 옆 폴더의 작업 트리가 아니라 이 태그의 내용을 넣는다(헤드 점검 2026-11-05: 판과 위치를 정해 둔다)
SURVEY_SCHEMA_TAG="${SURVEY_SCHEMA_TAG:-survey-schema-v4.1.1}"
PY="python3"; SCHEMA_ROOT=""
while [ $# -gt 0 ]; do case "$1" in --python) PY="$2"; shift 2;; --schema-root) SCHEMA_ROOT="$2"; shift 2;; *) echo "알 수 없는 인자 $1" >&2; exit 64;; esac; done
if [ -z "$SCHEMA_ROOT" ]; then
  # 위치 순서: 환경 변수 SURVEY_SERVICE_REPO → 옆 폴더(그 태그가 있을 때) → GitHub 에서 그 태그를 얕게 clone(smoke.sh 의 계약 소스와 같은 순서)
  SS_SRC="$(mktemp -d)"
  REPO="${SURVEY_SERVICE_REPO:-$ROOT/../survey-service}"
  if ! { git -C "$REPO" archive "$SURVEY_SCHEMA_TAG" schema 2>/dev/null | tar -x -C "$SS_SRC" 2>/dev/null && [ -d "$SS_SRC/schema/schemas" ]; }; then
    rm -rf "$SS_SRC"; SS_SRC="$(mktemp -d)"
    git clone -q --depth 1 --branch "$SURVEY_SCHEMA_TAG" git@github.com:Sdabin1209/openit_survey-service.git "$SS_SRC/repo" \
      || { echo "문진 스키마 $SURVEY_SCHEMA_TAG 를 옆 폴더에서도 GitHub 에서도 꺼내지 못했다" >&2; exit 1; }
    mv "$SS_SRC/repo/schema" "$SS_SRC/schema"; rm -rf "$SS_SRC/repo"
  fi
  SCHEMA_ROOT="$SS_SRC/schema"
fi
[ -d "$SCHEMA_ROOT/schemas" ] || { echo "문진 스키마가 없다: $SCHEMA_ROOT/schemas" >&2; exit 1; }
BC_DIR="$("$PY" -I -c "import boundary_contracts,os;print(os.path.dirname(boundary_contracts.__file__))")" || { echo "이 파이썬에 boundary_contracts가 없다(--python)" >&2; exit 1; }
BC_VER="$("$PY" -I -c "import importlib.metadata as m;print(m.version('boundary-contracts'))")"
TMP="$(mktemp -d)"; trap 'rm -rf -- "${TMP:?}" ${SS_SRC:+"$SS_SRC"}' EXIT
K="$TMP/mr-kit"; mkdir -p "$K/src" "$K/pylib" "$K/survey-schema" "$K/scripts"
cp -r "$ROOT/src/model_runtime" "$K/src/"; cp -r "$BC_DIR" "$K/pylib/"; cp -r "$SCHEMA_ROOT/schemas" "$SCHEMA_ROOT/CONTRACT_VERSION" "$K/survey-schema/"; [ -d "$SCHEMA_ROOT/data" ] && cp -r "$SCHEMA_ROOT/data" "$K/survey-schema/"   # 측정이 쓰는 흐름표(data/question_flow.json)
cp -r "$ROOT/profiles" "$K/"; cp "$ROOT/requirements-gpu.lock" "$K/"; cp -r "$ROOT/scripts/gpu_session" "$K/scripts/"
find "$K" -name __pycache__ -type d -prune -exec rm -rf {} +
{ echo "commit: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)$([ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ] && echo -dirty)";   # 파일에 해시를 직접 쓰지 않고 만드는 시점의 git에서 읽는다. 커밋 안 된 변경이 있으면 -dirty echo "boundary-contracts: $BC_VER"; echo "survey schema CONTRACT_VERSION: $(cat "$SCHEMA_ROOT/CONTRACT_VERSION")"
  echo "boundary-contracts: $BC_VER"; echo "survey-schema: $([ -n "${SS_SRC:-}" ] && echo "$SURVEY_SCHEMA_TAG" || echo "폴더 $SCHEMA_ROOT(--schema-root)")"
  (cd "$K" && find . -type f | sort | xargs sha256sum | sha256sum | sed 's/^/tree sha256: /'); } > "$K/KIT_MANIFEST.txt"
tar -czf "$OUT" -C "$TMP" mr-kit
echo "키트: $OUT ($(du -h "$OUT" | cut -f1))"; cat "$K/KIT_MANIFEST.txt"
