#!/usr/bin/env bash
# design.md §11 GPU 시험을 gpu-dev에서 돌린다 (D47 규칙: docs/brief.md 끝). 사용: scripts/gpu_check.sh [preflight|sync|setup|run|serve|fetch|all]
# 종료 코드: 0 성공, 3 호스트에 pip 없음(사용자가 python3-pip python3-venv 설치 필요), 4 호스트 불가(SSH 실패 2회), 1 시험 실패.
set -u
HOST=gpu-dev
REMOTE='~/openit-gpu/model-runtime'
LOCK='~/openit-gpu/.gpu.lock'
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WS="$(cd "$HERE/.." && pwd)"
KEX="-o KexAlgorithms=curve25519-sha256"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 $KEX "$HOST")
RSYNC=(rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=10 $KEX")
RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"

rssh() {  # 실패하면 한 번만 더 시도하고, 그래도 안 되면 멈춘다(반복 재시도 루프 금지)
  local rc
  "${SSH[@]}" "$@"; rc=$?
  if [ $rc -eq 255 ]; then sleep 5; "${SSH[@]}" "$@"; rc=$?; fi
  if [ $rc -eq 255 ]; then echo "호스트 불가: SSH 실패. docs/STATUS.md에 정리하고 멈춘다." >&2; exit 4; fi
  return $rc
}

preflight() {
  rssh 'python3 -m pip --version' >/dev/null 2>&1
  case $? in
    0) ;;
    *) if ! "${SSH[@]}" true 2>/dev/null; then echo "호스트 불가: SSH" >&2; exit 4; fi
       echo "호스트 불가(pip 없음): 사용자가 'sudo apt install python3-pip python3-venv'를 해야 한다. 설치하지 않는다." >&2; exit 3 ;;
  esac
  rssh 'python3 -m venv --help >/dev/null 2>&1' || { echo "호스트 불가(venv 없음): python3-venv 필요" >&2; exit 3; }
  rssh 'nvidia-smi --query-gpu=name,memory.used,memory.total,driver_version --format=csv,noheader'
}

sync_code() {   # 루트 통째 rsync 금지: 폴더별로 보낸다. 비밀 값은 보내지 않는다.
  rssh "mkdir -p $REMOTE/vendor"
  for d in src scripts profiles tests; do
    "${RSYNC[@]}" --exclude __pycache__ --exclude '*.egg-info' "$HERE/$d/" "$HOST:openit-gpu/model-runtime/$d/" || exit 1
  done
  "${RSYNC[@]}" "$HERE/pyproject.toml" "$HERE/requirements-gpu.lock" "$HOST:openit-gpu/model-runtime/" || exit 1
  # 읽기 전용 입력의 사본(비밀 아님): 해시 구현과 문진 스키마. 호스트는 GitHub에 접속하지 않는다.
  # 계약은 태그 기준이다. 형제 폴더가 그 태그가 아니면 보내지 않는다(예외 없음).
  BOUNDARY_TAG="${BOUNDARY_TAG:-v2.0.1}"
  [ "$(git -C "$WS/boundary-contracts" describe --tags --exact-match 2>/dev/null || true)" = "$BOUNDARY_TAG" ] || { echo "boundary-contracts가 태그 $BOUNDARY_TAG가 아니다. 보내지 않는다" >&2; exit 1; }
  "${RSYNC[@]}" --exclude .git --exclude __pycache__ --exclude '*.egg-info' "$WS/boundary-contracts/" "$HOST:openit-gpu/model-runtime/vendor/boundary-contracts/" || exit 1
  "${RSYNC[@]}" --exclude build --exclude '*.egg-info' --exclude __pycache__ "$WS/survey-service/schema/" "$HOST:openit-gpu/model-runtime/vendor/schema/" || exit 1
}

setup_env() {   # GPU를 쓰지 않는다. 설치 버전을 runs/env-versions.txt에 남긴다
  rssh "cd $REMOTE && { [ -d .venv ] || python3 -m venv .venv; } && .venv/bin/python -m pip install -q -U pip \
    && .venv/bin/python -m pip install -q -e vendor/boundary-contracts -e '.[vllm]' safetensors \
    && mkdir -p runs && .venv/bin/python -m pip freeze > runs/env-versions.txt" || { echo "설치 실패" >&2; exit 1; }
}

run_probe() {
  local d="runs/$RUN_ID"
  rssh "cd $REMOTE && mkdir -p $d && setsid nohup bash -c 'for i in 1 2; do flock -w 1800 $LOCK timeout 1500 .venv/bin/python scripts/gpu_probe.py --out $d --tag run\$i --profile profiles/gpu-dev-12g.json --schema-dir vendor/schema/schemas > $d/vllm-run\$i.log 2>&1; done; flock -w 1800 $LOCK timeout 900 .venv/bin/python scripts/gpu_probe.py --out $d --tag baseline --no-lora --profile profiles/gpu-dev-12g.json --schema-dir vendor/schema/schemas > $d/vllm-baseline.log 2>&1; echo done > $d/DONE' >/dev/null 2>&1 &"
  echo "시작: $d (분리 실행). 폴링한다."
  local n=0
  while [ $n -lt 120 ]; do   # 최대 약 60분
    sleep 30; n=$((n+1))
    if rssh "test -f $REMOTE/$d/DONE"; then return 0; fi
  done
  echo "시간 초과: 호스트에서 계속 돌 수 있다. 나중에 fetch" >&2; return 1
}

fetch() {
  mkdir -p "$HERE/runs"
  "${RSYNC[@]}" "$HOST:openit-gpu/model-runtime/runs/" "$HERE/runs/" || exit 1
  (cd "$HERE" && .venv/bin/python scripts/gpu_report.py "runs/$RUN_ID") || true
}

case "${1:-all}" in
  preflight) preflight ;;
  sync) preflight >/dev/null && sync_code ;;
  setup) preflight >/dev/null && setup_env ;;
  run) preflight >/dev/null && run_probe ;;
  serve) preflight >/dev/null && rssh "cd $REMOTE && mkdir -p runs/$RUN_ID && flock -w 1800 $LOCK timeout 1500 .venv/bin/python scripts/gpu_serve_check.py --out runs/$RUN_ID/serve --adapters-root \${ADAPTERS_ROOT:-$HOME/openit-gpu/model-lab/runs-ref/run-0a26438d64de4d71} --samples-root \${ADAPTERS_ROOT:-$HOME/openit-gpu/model-lab/runs-ref/run-0a26438d64de4d71}/data --schema-dir vendor/schema/schemas --profile profiles/gpu-dev-12g.json" ;;
  fetch) fetch ;;
  all) preflight && sync_code && setup_env && run_probe && fetch ;;
  *) echo "사용: $0 [preflight|sync|setup|run|serve|fetch|all]" >&2; exit 2 ;;
esac
