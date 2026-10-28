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
# 왜 선택 설치인가: turn-engine은 시험 전용이고 이 리포가 버전을 핀하지 않는다(U15). 모방 컴포넌트 시험(tests/test_mimic.py)은 turn-engine의 validate_extraction으로
# 출력을 확인해야 하므로 --with-turn-engine로 설치한다(시험도 없으면 스스로 설치한다, tests/helpers.py require_turn_engine). 태그는 TURN_ENGINE_TAG로 정한다.
# 기본 v0.4.1은 README가 마지막으로 시험한 태그다. 워크스페이스 기준표는 deploy/scripts/lib.sh이며 다르면 TURN_ENGINE_TAG를 그 값으로 준다.
if [ "${1:-}" = "--with-turn-engine" ]; then
  .venv/bin/python -m pip install -q --no-deps "git+ssh://git@github.com/Sdabin1209/openit_turn-engine.git@${TURN_ENGINE_TAG:-v0.4.1}"
fi
echo "setup-env: 완료 (boundary-contracts ${TAG}). 시험: .venv/bin/python -m pytest -q -rs"
