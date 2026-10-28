#!/usr/bin/env bash
# 왜: 이 영역의 개발·시험 환경 준비 방법의 정본은 이 파일 하나다. 새 머신으로 옮길 때 README·runbook마다 설치법이 달라 어긋났기 때문에 한 곳으로 모았다.
# 계약 태그는 Dockerfile의 `ARG BOUNDARY_VERSION=`에서만 읽는다(이 파일에 버전 숫자를 따로 적지 않는다). 비밀 값은 다루지 않는다.
# 사용: bash scripts/setup-env.sh   (어느 폴더에서 실행해도 된다. 여러 번 실행해도 안전하다)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TAG="$(sed -n 's/^ARG BOUNDARY_VERSION=\(v[0-9][0-9.]*\).*/\1/p' Dockerfile | head -1)"
[ -n "$TAG" ] || { echo "setup-env: Dockerfile에서 ARG BOUNDARY_VERSION 값을 읽지 못했다" >&2; exit 2; }
BC_URL="git+ssh://git@github.com/Sdabin1209/openit_boundary-contracts.git@${TAG}"

[ -x .venv/bin/python ] || python3 -m venv .venv   # 있으면 재사용한다(멱등)

install() { .venv/bin/python -m pip install -q pytest jsonschema "$BC_URL" -e .; }
if ! install; then
  echo "setup-env: 설치 실패(네트워크 또는 GitHub 접근 문제일 수 있다). 5초 뒤 한 번 재시도한다" >&2
  sleep 5
  install || { echo "setup-env: 재시도도 실패했다. 계약 태그 ${TAG}를 git+ssh로 받지 못했거나 PyPI에 닿지 못했다(GitHub SSH 키와 네트워크 확인)" >&2; exit 1; }
fi
echo "setup-env: 완료 (boundary-contracts ${TAG}). 시험: .venv/bin/python -m pytest -q -rs"
