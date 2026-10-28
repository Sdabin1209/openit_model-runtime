#!/usr/bin/env bash
# [PC에서] 서버로 올릴 키트 tar.gz를 만든다. 서버는 인터넷/GitHub 없이 이것만 풀어 PYTHONPATH로 쓴다(ADR 0013).
# 내용: mr-kit/src(model_runtime), mr-kit/pylib(boundary_contracts: 설치된 태그 패키지 그대로, 스키마 포함), mr-kit/survey-schema(문진 스키마 schemas/, CONTRACT_VERSION),
#       mr-kit/profiles, mr-kit/scripts/gpu_session, mr-kit/KIT_MANIFEST.txt(커밋·boundary 버전·파일 해시). 데이터(C1)와 어댑터는 넣지 않는다.
# 사용: make_kit.sh OUT.tar.gz [--python PY] [--schema-root DIR(survey-service/schema)]
# 서버 쪽: mkdir -p ~/dabin && tar -xzf OUT.tar.gz -C ~/dabin   (-> ~/dabin/mr-kit)
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?출력 tar.gz 경로}"; shift || true
PY="python3"; SCHEMA_ROOT="$ROOT/../survey-service/schema"; [ -d "$SCHEMA_ROOT/schemas" ] || SCHEMA_ROOT="$ROOT/../../survey-service/schema"
while [ $# -gt 0 ]; do case "$1" in --python) PY="$2"; shift 2;; --schema-root) SCHEMA_ROOT="$2"; shift 2;; *) echo "알 수 없는 인자 $1" >&2; exit 64;; esac; done
[ -d "$SCHEMA_ROOT/schemas" ] || { echo "문진 스키마가 없다: $SCHEMA_ROOT/schemas" >&2; exit 1; }
BC_DIR="$("$PY" -I -c "import boundary_contracts,os;print(os.path.dirname(boundary_contracts.__file__))")" || { echo "이 파이썬에 boundary_contracts가 없다(--python)" >&2; exit 1; }
BC_VER="$("$PY" -I -c "import importlib.metadata as m;print(m.version('boundary-contracts'))")"
TMP="$(mktemp -d)"; trap 'rm -rf -- "${TMP:?}"' EXIT
K="$TMP/mr-kit"; mkdir -p "$K/src" "$K/pylib" "$K/survey-schema" "$K/scripts"
cp -r "$ROOT/src/model_runtime" "$K/src/"; cp -r "$BC_DIR" "$K/pylib/"; cp -r "$SCHEMA_ROOT/schemas" "$SCHEMA_ROOT/CONTRACT_VERSION" "$K/survey-schema/"
cp -r "$ROOT/profiles" "$K/"; cp "$ROOT/requirements-gpu.lock" "$K/"; cp -r "$ROOT/scripts/gpu_session" "$K/scripts/"
find "$K" -name __pycache__ -type d -prune -exec rm -rf {} +
{ echo "commit: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"; echo "boundary-contracts: $BC_VER"; echo "survey schema CONTRACT_VERSION: $(cat "$SCHEMA_ROOT/CONTRACT_VERSION")"
  (cd "$K" && find . -type f | sort | xargs sha256sum | sha256sum | sed 's/^/tree sha256: /'); } > "$K/KIT_MANIFEST.txt"
tar -czf "$OUT" -C "$TMP" mr-kit
echo "키트: $OUT ($(du -h "$OUT" | cut -f1))"; cat "$K/KIT_MANIFEST.txt"
