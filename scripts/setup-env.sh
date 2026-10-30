#!/usr/bin/env bash
# 왜: 이 영역의 개발·시험 환경 준비 방법의 정본은 이 파일 하나다. 새 머신으로 옮길 때 README·runbook마다 설치법이 달라 어긋났기 때문에 한 곳으로 모았다.
# 계약 태그는 Dockerfile의 `ARG BOUNDARY_VERSION=`에서만 읽는다(이 파일에 버전 숫자를 따로 적지 않는다). 비밀 값은 다루지 않는다.
# 사용: bash scripts/setup-env.sh   (어느 폴더에서 실행해도 된다. 인자 없음. 여러 번 실행해도 안전하다)
# 출력(계약 v2.6.0 SetupEnvReport, OPEN_GAPS U43, ADR 0026): 표준 출력의 마지막이자 유일한 줄은 결과 JSON 한 줄이다. 진행 기록(pip 포함)은 모두 표준 오류로 간다.
#   deploy는 environment.test_command만 읽고 이 영역의 .venv 경로나 시험 도구를 짐작하지 않는다(구조 규칙 1). 실패해도(종료 코드 0이 아님) 보고 한 줄을 낸다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

AREA="model-runtime"   # 계약 SetupEnvReport.area: 영역(리포) 이름
report() {   # report <status> <exit_code> [환경 선언 포함 여부]. seconds는 bash 내장 SECONDS(이 스크립트가 시작한 뒤 경과한 초)
  local env_json=""
  [ "${3:-}" = env ] && env_json=',"environment":{"kind":"python-venv","path":".venv","test_command":[".venv/bin/python","-m","pytest","-q"]}'
  printf '{"area":"%s","status":"%s","exit_code":%s,"seconds":%s%s}\n' "$AREA" "$1" "$2" "$SECONDS" "$env_json"
}
trap 'rc=$?; [ "$rc" -eq 0 ] || report fail "$rc"' EXIT   # 어디서 실패하든 마지막 줄은 보고다(준비되지 못한 환경은 선언하지 않는다)

TAG="$(sed -n 's/^ARG BOUNDARY_VERSION=\(v[0-9][0-9.]*\).*/\1/p' Dockerfile | head -1)"
[ -n "$TAG" ] || { echo "setup-env: Dockerfile에서 ARG BOUNDARY_VERSION 값을 읽지 못했다" >&2; exit 2; }
BC_URL="git+ssh://git@github.com/Sdabin1209/openit_boundary-contracts.git@${TAG}"
LINK_TAG="$(sed -n 's/^ARG LINK_VERSION=\(v[0-9][0-9.]*\).*/\1/p' Dockerfile | head -1)"   # 통신 기반 라이브러리(D118). 정본은 Dockerfile의 ARG LINK_VERSION
[ -n "$LINK_TAG" ] || { echo "setup-env: Dockerfile에서 ARG LINK_VERSION 값을 읽지 못했다" >&2; exit 2; }
LINK_URL="git+ssh://git@github.com/Sdabin1209/openit_link.git@${LINK_TAG}"

[ -x .venv/bin/python ] || python3 -m venv .venv >&2   # 있으면 재사용한다(멱등)

install() { .venv/bin/python -m pip install -q pytest jsonschema "$BC_URL" "$LINK_URL" -e . >&2; }
if ! install; then
  echo "setup-env: 설치 실패(네트워크 또는 GitHub 접근 문제일 수 있다). 5초 뒤 한 번 재시도한다" >&2
  sleep 5
  install || { echo "setup-env: 재시도도 실패했다. 계약 태그 ${TAG}를 git+ssh로 받지 못했거나 PyPI에 닿지 못했다(GitHub SSH 키와 네트워크 확인)" >&2; exit 1; }
fi
# 시험이 쓰는 문항 흐름표는 문진 스키마(../survey-service/schema/data/question_flow.json)에서 읽는다. 사본을 두지 않으므로 여기서 설치하는 것은 없다
# (survey_schema가 설치돼 있거나 SURVEY_SCHEMA_DIR이 가리키거나 상위 폴더에 survey-service/schema가 있으면 쓰고, 없으면 해당 시험만 건너뛴다).
echo "setup-env: 완료 (boundary-contracts ${TAG}). 시험: .venv/bin/python -m pytest -q -rs" >&2
trap - EXIT
report ok 0 env
