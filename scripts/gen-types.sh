#!/usr/bin/env bash
# 왜: types/는 schemas/에서 생성한 코드 타입이고 손으로 고치지 않는다(DESIGN.md 3절, README 4절). 생성 도구는 requirements-codegen.lock으로 고정한다.
# 사용: bash scripts/gen-types.sh [출력 폴더, 기본 src/model_runtime/types]   .venv-codegen을 만들거나 재사용한다(멱등).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="${1:-src/model_runtime/types}"
# 여러 시험 프로세스가 동시에 처음 만들어도 부딪히지 않게 잠금으로 직렬화한다(없을 때만 만든다)
( flock 9; [ -x .venv-codegen/bin/datamodel-codegen ] || { python3 -m venv .venv-codegen && .venv-codegen/bin/pip install -q -r requirements-codegen.lock; } ) 9>.venv-codegen.lock
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
TMP="$TMPD/schemas"; mkdir "$TMP"   # 폴더 이름을 고정한다(생성물 머리말에 입력 폴더 이름이 들어간다)
cp schemas/*.json "$TMP/"   # examples/는 스키마가 아니므로 생성 입력에서 뺀다
rm -rf "$OUT"
# 옵션의 왜: enum을 Literal로(역할 등이 문자열 그대로 오가야 한다), 기본값 유지(CallInfo.outcome 등), frozen(옛 공개 타입이 frozen dataclass였다)
.venv-codegen/bin/datamodel-codegen --input "$TMP" --input-file-type jsonschema --output "$OUT" --output-model-type dataclasses.dataclass \
  --frozen-dataclasses --target-python-version 3.10 --disable-timestamp --enum-field-as-literal all --use-default --formatters black isort 2>/dev/null
