#!/usr/bin/env bash
# GPU 세션 한 번: preflight -> 환경 변수(캐시 전부 쓰기 허용 폴더 안) -> 번들 준비 -> 서버 기동 -> 적재 -> 측정 -> 서버 종료와 정리 -> 요약.
# 서버에서 사람이 실행한다(docs/GPU_SESSION.md). 환경(conda env)은 수정하지 않는다: 키트 폴더를 PYTHONPATH로 얹는다(ADR 0013).
# 쓰기는 WRITE_ROOT(기본 $HOME/dabin) 안에서만 한다. 남의 프로세스는 건드리지 않는다: 이 스크립트가 직접 띄운 프로세스 그룹만 정리한다.
#
# 필수: --adapter-a DIR --adapter-b DIR --c1-dir DIR(lora_a_val.jsonl, lora_b_val.jsonl이 있는 폴더)
# 선택:
#   --kit DIR            키트 루트(src/, pylib/, survey-schema/, profiles/, scripts/가 있는 폴더). 기본: ~/dabin/mr-kit가 있으면 그것, 없으면 이 스크립트 기준 리포 루트(개발)
#   --work DIR           작업 폴더(기본 $WRITE_ROOT/mr-session). 실행마다 하위 run-<시각> 폴더를 만든다
#   --python PATH        파이썬(기본 /home/openit/miniconda3/envs/openit-llm/bin/python)
#   --until HH:MM        서버 시각 기준 사용 상한. 지나면 시작을 거부하고, 측정 시간 상한도 이 시각에서 역산한다
#   --budget-min N       이 실행 전체의 시간 상한(분, 기본 40). --until이 더 이르면 그쪽
#   --ready-timeout-min N  서버 준비 대기 상한(분, 기본 15)
#   --profile FILE       기본 <kit>/profiles/gpu-shared-8b.json
#   --util X             gpu_memory_utilization 덮어쓰기(기본 프로파일 값 0.30)
#   --n-a N --n-b N      측정 행 수(기본 각 200)     --concurrency 1,4  동시 요청 수 목록(기본 1,4)
#   --max-used-mib N     preflight 임계(기본 2048)    --margin-mib N  남은 메모리 여유(기본 4096)
#   --production-limits  요청 상한 3.5초/대기 3.0초 기본값 그대로 측정(기본은 상한을 풀어 순수 지연을 잰다)
#   --backend vllm|fake  fake는 리허설용(GPU 없이 파이프라인 확인; MODEL_RUNTIME_ALLOW_FAKE=1을 이 스크립트가 켠다)
#   --keep-bundle        끝에 번들 복사본(어댑터 약 350MB)을 지우지 않는다    --clean-cache  끝에 캐시도 지운다
# 종료 코드: preflight 거부 2 / nvidia-smi 불가 3 / 사용법 64 / 서버 기동·적재 실패 5 / 시간 초과 6 / 그 밖에는 measure.py의 종료 코드(0, 3, 4)
set -u -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DEFAULT="$(cd "$HERE/../.." && pwd)"
WRITE_ROOT="${GPU_SESSION_WRITE_ROOT:-$HOME/dabin}"
SMI="${NVIDIA_SMI:-nvidia-smi}"
PY="/home/openit/miniconda3/envs/openit-llm/bin/python"
KIT=""; WORK=""; UNTIL=""; BUDGET_MIN=40; READY_MIN=15; PROFILE=""; UTIL=""; N_A=200; N_B=200; CONC="1,4"
MAX_USED=2048; MARGIN=4096; PROD=0; BACKEND=vllm; KEEP_BUNDLE=0; CLEAN_CACHE=0; AD_A=""; AD_B=""; C1=""

die() { echo "[run_session] 오류: $*" >&2; exit "${2:-64}"; }
log() { echo "[run_session $(date +%H:%M:%S)] $*"; }
while [ $# -gt 0 ]; do
  case "$1" in   # 값이 필요한 옵션이 마지막이면 shift 2가 실패해 무한 루프가 된다(MR-3): 사용법을 내고 종료 코드 2
    --kit|--work|--python|--until|--budget-min|--ready-timeout-min|--profile|--util|--n-a|--n-b|--concurrency|--max-used-mib|--margin-mib|--adapter-a|--adapter-b|--c1-dir|--backend)
      [ $# -ge 2 ] || { echo "[run_session] 오류: $1 에 값이 필요하다(--help로 사용법)" >&2; exit 2; } ;;
  esac
  case "$1" in
    --kit) KIT="${2:-}"; shift 2 ;; --work) WORK="${2:-}"; shift 2 ;; --python) PY="${2:-}"; shift 2 ;;
    --until) UNTIL="${2:-}"; shift 2 ;; --budget-min) BUDGET_MIN="${2:-}"; shift 2 ;; --ready-timeout-min) READY_MIN="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;; --util) UTIL="${2:-}"; shift 2 ;; --n-a) N_A="${2:-}"; shift 2 ;; --n-b) N_B="${2:-}"; shift 2 ;;
    --concurrency) CONC="${2:-}"; shift 2 ;; --max-used-mib) MAX_USED="${2:-}"; shift 2 ;; --margin-mib) MARGIN="${2:-}"; shift 2 ;;
    --adapter-a) AD_A="${2:-}"; shift 2 ;; --adapter-b) AD_B="${2:-}"; shift 2 ;; --c1-dir) C1="${2:-}"; shift 2 ;;
    --backend) BACKEND="${2:-}"; shift 2 ;; --production-limits) PROD=1; shift ;; --keep-bundle) KEEP_BUNDLE=1; shift ;; --clean-cache) CLEAN_CACHE=1; shift ;;
    -h|--help) sed -n 2,28p "$0"; exit 0 ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done
[ -n "$AD_A" ] && [ -n "$AD_B" ] && [ -n "$C1" ] || die "--adapter-a, --adapter-b, --c1-dir가 필요하다"
[ -d "$AD_A" ] && [ -d "$AD_B" ] || die "어댑터 디렉터리가 없다"
[ -f "$C1/lora_a_val.jsonl" ] && [ -f "$C1/lora_b_val.jsonl" ] || die "$C1 에 lora_a_val.jsonl, lora_b_val.jsonl이 없다"
case "$BACKEND" in vllm|fake) ;; *) die "--backend는 vllm 또는 fake" ;; esac
[ -x "$PY" ] || die "파이썬이 없다: $PY (--python)"
if [ -z "$KIT" ]; then if [ -d "$HOME/dabin/mr-kit/src" ]; then KIT="$HOME/dabin/mr-kit"; else KIT="$KIT_DEFAULT"; fi; fi
[ -z "$WORK" ] && WORK="$WRITE_ROOT/mr-session"
[ -z "$PROFILE" ] && PROFILE="$KIT/profiles/gpu-shared-8b.json"
[ -f "$PROFILE" ] || die "프로파일이 없다: $PROFILE"
SCHEMA_DIR="$KIT/survey-schema/schemas"
[ -d "$SCHEMA_DIR" ] || SCHEMA_DIR="${MODEL_RUNTIME_SCHEMA_DIR:-$KIT/tests/fixtures/schema/schemas}"
[ -d "$SCHEMA_DIR" ] || die "문진 스키마 폴더가 없다(키트의 survey-schema/schemas)"
PYLIB="$KIT/pylib"; [ -d "$PYLIB" ] || PYLIB=""

# 쓰기 규칙: 쓰는 모든 경로가 WRITE_ROOT 안인지 확인한다(심볼릭 링크를 푼 뒤)
mkdir -p "$WRITE_ROOT" 2>/dev/null || die "$WRITE_ROOT 를 만들 수 없다"
ROOT_REAL="$(cd "$WRITE_ROOT" && pwd -P)"
# 만들기 전에 검사한다(MR-1): 거부되면 WRITE_ROOT 밖에 아무것도 만들지 않는다. realpath -m은 없는 경로도 기존 심볼릭 링크를 풀어 정규화한다.
WORK_REAL="$(realpath -m -- "$WORK" 2>/dev/null)" || die "작업 폴더 경로를 해석하지 못했다: $WORK"
case "$WORK_REAL/" in "$ROOT_REAL"/*) ;; *) die "작업 폴더 $WORK_REAL 는 쓰기 허용 폴더($ROOT_REAL) 밖이다. 서버에서는 ~/dabin 안만 쓸 수 있다" ;; esac
[ "$WORK_REAL" != "$ROOT_REAL" ] || die "작업 폴더는 쓰기 허용 폴더 자체가 아니라 그 안의 하위 폴더여야 한다"

mkdir -p "$WORK_REAL" 2>/dev/null || die "$WORK_REAL 를 만들 수 없다"
START_S=$(date +%s)
BUDGET_S=$(( BUDGET_MIN * 60 ))
if [ -n "$UNTIL" ]; then
  until_s=$(date -d "today $UNTIL" +%s 2>/dev/null) || die "--until 시각을 해석하지 못했다: $UNTIL"
  [ $(( until_s - START_S )) -lt "$BUDGET_S" ] && BUDGET_S=$(( until_s - START_S ))
fi
remaining() { echo $(( START_S + BUDGET_S - $(date +%s) )); }

# 이 실행용 프로파일(gpu_memory_utilization 덮어쓰기 반영)을 실행 폴더에 쓴다
RUN="$WORK_REAL/run-$(date +%Y%m%d-%H%M%S)"
C="$WORK_REAL/cache"
CACHE_MARK=".mr-session-owned"   # 이 키트가 만든 캐시라는 표식. 없는 폴더는 건드리지 않는다(MR-2)
if [ -e "$C" ] || [ -L "$C" ]; then
  if [ -L "$C" ] || [ ! -f "$C/$CACHE_MARK" ]; then
    if [ -L "$C" ] || [ -n "$(ls -A "$C" 2>/dev/null)" ]; then die "$C 가 이미 있고 이 키트가 만든 캐시가 아니다(표식 $CACHE_MARK 없음). 다른 --work를 주거나 그 폴더를 옮긴다. 아무것도 만들지 않았다" 64; fi
  fi
fi
mkdir -p "$RUN/tmp" "$RUN/bundles" "$RUN/logs" "$C" || die "실행 폴더를 만들 수 없다"
: > "$C/$CACHE_MARK"; echo "$$" > "$C/.active.$$"   # 이 실행이 캐시를 쓰는 동안 표시(다른 실행이 살아 있으면 지우지 않는다)
RUN_PROFILE="$RUN/profile.json"
"$PY" -I - "$PROFILE" "$RUN_PROFILE" "$UTIL" <<'PYEOF' || die "프로파일을 쓰지 못했다"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
if sys.argv[3]:
    p["engine"]["gpu_memory_utilization"] = float(sys.argv[3])
json.dump(p, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PYEOF
UTIL_EFF="$("$PY" -I -c "import json,sys;print(json.load(open(sys.argv[1]))['engine'].get('gpu_memory_utilization',''))" "$RUN_PROFILE")"
log "실행 폴더 $RUN, 프로파일 gpu_memory_utilization=$UTIL_EFF, 시간 상한 $(( BUDGET_S / 60 ))분"

# 1) preflight (필요한 남은 메모리 = util x 전체 + 여유)
MINFREE=0
if [ "$BACKEND" = vllm ]; then
  total=$("$SMI" --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [ -n "$total" ]; then MINFREE=$("$PY" -I -c "import sys;print(int(float(sys.argv[1])*int(sys.argv[2]))+int(sys.argv[3]))" "$UTIL_EFF" "$total" "$MARGIN"); fi
fi
PF=("$HERE/preflight.sh" --max-used-mib "$MAX_USED" --min-free-mib "$MINFREE"); [ -n "$UNTIL" ] && PF+=(--until "$UNTIL")
NVIDIA_SMI="$SMI" bash "${PF[@]}" | tee "$RUN/logs/preflight.log"; rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { log "preflight가 실행을 거부했다(종료 코드 $rc). 아무것도 시작하지 않았다."; exit "$rc"; }

# 2) 환경 변수: 모든 캐시·임시를 작업 폴더로. HOME은 그대로 둔다
mkdir -p "$C/vllm" "$C/xdg" "$C/inductor" "$C/triton" "$C/flashinfer" "$C/cuda" "$C/numba" "$C/mpl" "$C/vllm-config" "$C/torch-ext"
export VLLM_CACHE_ROOT="$C/vllm" XDG_CACHE_HOME="$C/xdg" TORCHINDUCTOR_CACHE_DIR="$C/inductor" TRITON_CACHE_DIR="$C/triton" \
       FLASHINFER_WORKSPACE_BASE="$C/flashinfer" CUDA_CACHE_PATH="$C/cuda" NUMBA_CACHE_DIR="$C/numba" MPLCONFIGDIR="$C/mpl" \
       VLLM_CONFIG_ROOT="$C/vllm-config" TORCH_EXTENSIONS_DIR="$C/torch-ext" TMPDIR="$RUN/tmp" \
       HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 PYTHONDONTWRITEBYTECODE=1 \
       PYTHONPATH="$KIT/src${PYLIB:+:$PYLIB}${PYTHONPATH:+:$PYTHONPATH}"
[ "$BACKEND" = fake ] && export MODEL_RUNTIME_ALLOW_FAKE=1

SERVER_PID=""; REL_PID=""; SMP_PID=""
gpu_mem() { if command -v "$SMI" >/dev/null 2>&1; then "$SMI" --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null | head -1; fi; }
cleanup() {
  trap - EXIT INT TERM
  log "정리 시작(이 스크립트가 띄운 프로세스 그룹만 종료한다)"
  [ -n "$SMP_PID" ] && kill "$SMP_PID" 2>/dev/null
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null   # setsid로 띄웠으므로 pgid == pid. 서버가 SIGTERM에서 엔진을 내린다
    for _ in $(seq 1 40); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
    if kill -0 "$SERVER_PID" 2>/dev/null; then log "서버가 40초 안에 안 내려가 그룹을 강제 종료한다"; fi
  fi
  [ -n "$SERVER_PID" ] && kill -KILL -- "-$SERVER_PID" 2>/dev/null   # 남은 자식(EngineCore 등)
  [ -n "$REL_PID" ] && kill "$REL_PID" 2>/dev/null
  wait 2>/dev/null
  if [ "$KEEP_BUNDLE" -eq 0 ]; then rm -rf -- "${RUN:?}/release" "${RUN:?}/bundles" 2>/dev/null; fi
  rm -rf -- "${RUN:?}/tmp" 2>/dev/null
  rm -f -- "${C:?}/.active.$$" 2>/dev/null
  if [ "$CLEAN_CACHE" -eq 1 ]; then
    others=0
    for f in "${C:?}"/.active.*; do [ -e "$f" ] || continue; p="${f##*.active.}"; if kill -0 "$p" 2>/dev/null; then others=1; fi; done
    if [ -L "$C" ] || [ ! -f "${C:?}/$CACHE_MARK" ]; then log "캐시 $C 에 표식이 없어 지우지 않는다"
    elif [ "$others" -eq 1 ]; then log "같은 캐시를 쓰는 다른 실행이 살아 있어 지우지 않는다"
    else rm -rf -- "${C:?}" 2>/dev/null; fi
  fi
  after="$(gpu_mem)"
  if [ -n "$after" ]; then echo "$(date +%H:%M:%S) 종료 후 $after" >> "$RUN/logs/gpu_mem.log"; log "종료 후 GPU 메모리: $after (logs/gpu_mem.log의 '시작 전'과 비교한다)"; fi
  log "남은 것: $RUN (logs/, measure.json, measure.txt, profile.json). 캐시는 $C"
}
trap cleanup EXIT
trap 'log "신호를 받아 중단한다"; exit 130' INT TERM

echo "$(date +%H:%M:%S) 시작 전 $(gpu_mem)" >> "$RUN/logs/gpu_mem.log"
( while true; do echo "$(date +%H:%M:%S) $(gpu_mem) | $("$SMI" --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null | tr '\n' ';')" >> "$RUN/logs/gpu_mem.log"; sleep 15; done ) &
SMP_PID=$!

# 3) 번들 준비
VLLM_VER="$("$PY" -I -c "import importlib.metadata as m;print(m.version('vllm'))" 2>/dev/null || true)"
if [ "$BACKEND" = vllm ] && [ -z "$VLLM_VER" ]; then die "이 파이썬에서 vllm 버전을 읽지 못했다($PY)" 5; fi
PB=("$PY" "$HERE/prepare_bundle.py" --adapter-a "$AD_A" --adapter-b "$AD_B" --c1-a "$C1/lora_a_val.jsonl" --c1-b "$C1/lora_b_val.jsonl" --out "$RUN/release" --profile "$RUN_PROFILE" --backend "$BACKEND" --release-id gpu-session)
[ "$BACKEND" = vllm ] && PB+=(--rewrite-base-name --vllm-version "$VLLM_VER")
log "번들 준비 (vllm=${VLLM_VER:-없음})"
"${PB[@]}" > "$RUN/logs/prepare_bundle.log" 2>&1 || { cat "$RUN/logs/prepare_bundle.log"; die "번들 준비 실패" 5; }
cat "$RUN/logs/prepare_bundle.log"
cp "$RUN/release/PREPARE_REPORT.json" "$RUN/logs/PREPARE_REPORT.json"   # release/는 끝에 지워지므로 보고서는 logs에 남긴다

# 4) 릴리스 서버(I4 모양, 루프백)와 추론 서버. 토큰은 파일로 만든다(권한 600)
umask 077
TOKEN="$("$PY" -I -c "import secrets;print(secrets.token_hex(16))")"
printf 'measure=%s\n' "$TOKEN" > "$RUN/tokens"; printf '%s' "$TOKEN" > "$RUN/token"
printf 'Authorization: Bearer %s\n' "$TOKEN" > "$RUN/auth_header"; unset TOKEN   # curl -H @파일: 토큰이 프로세스 인자(ps)에 보이지 않는다
freeport() { "$PY" -I -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"; }
"$PY" "$HERE/release_server.py" "$RUN/release" --port 0 --port-file "$RUN/release.port" > "$RUN/logs/release_server.log" 2>&1 &
REL_PID=$!
for _ in $(seq 1 20); do [ -s "$RUN/release.port" ] && break; sleep 0.5; done
[ -s "$RUN/release.port" ] || die "릴리스 서버가 뜨지 않았다(logs/release_server.log)" 5
RELPORT="$(cat "$RUN/release.port")"; PORT="$(freeport)"
if [ "$PROD" -eq 1 ]; then LIMITS=(MODEL_RUNTIME_X=1); else LIMITS=(MODEL_RUNTIME_REQUEST_DEADLINE_S=600 MODEL_RUNTIME_QUEUE_WAIT_S=600); fi
log "추론 서버 기동 127.0.0.1:$PORT (백엔드 $BACKEND, 요청 상한 $([ "$PROD" -eq 1 ] && echo '기본 3.5s/3.0s' || echo '풀어 둠 600s'))"
env MODEL_RUNTIME_BACKEND="$BACKEND" MODEL_RUNTIME_SCHEMA_DIR="$SCHEMA_DIR" MODEL_RUNTIME_PROFILE="$RUN_PROFILE" \
    MODEL_RUNTIME_RELEASE_URL="http://127.0.0.1:$RELPORT" MODEL_RUNTIME_BUNDLE_DIR="$RUN/bundles" \
    MODEL_RUNTIME_TOKENS_FILE="$RUN/tokens" MODEL_RUNTIME_PUT_WAIT_S=5 MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S=600 \
    MODEL_RUNTIME_HOST=127.0.0.1 MODEL_RUNTIME_PORT="$PORT" MODEL_RUNTIME_BACKEND_RETRY_S=0 "${LIMITS[@]}" \
    setsid "$PY" -m model_runtime.server > "$RUN/logs/server.log" 2>&1 &
SERVER_PID=$!

# 5) 준비 대기(상한)
cap=$(( READY_MIN * 60 )); r=$(remaining); [ "$r" -lt "$cap" ] && cap=$r
t0=$(date +%s); ready=0
while [ $(( $(date +%s) - t0 )) -lt "$cap" ]; do
  kill -0 "$SERVER_PID" 2>/dev/null || { tail -n 30 "$RUN/logs/server.log"; die "서버 프로세스가 종료됐다(logs/server.log)" 5; }
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -H "@$RUN/auth_header" "http://127.0.0.1:$PORT/readyz" 2>/dev/null)
  if [ "$code" = 200 ]; then ready=1; break; fi
  sleep 3
done
[ "$ready" -eq 1 ] || { tail -n 30 "$RUN/logs/server.log"; die "서버가 ${cap}초 안에 준비되지 않았다(시간 상한)" 6; }
log "서버 준비됨 ($(( $(date +%s) - t0 ))초). GPU: $(gpu_mem)"

# 6) 측정(적재 포함). 남은 시간에서 정리 여유 90초를 뺀 만큼만 쓴다
left=$(( $(remaining) - 90 )); [ "$left" -lt 60 ] && die "측정할 시간이 남지 않았다(남은 ${left}초)" 6
log "적재와 측정 시작 (측정 시간 상한 ${left}초, 행 수 A=$N_A B=$N_B, 동시 $CONC)"
"$PY" "$HERE/measure.py" --url "http://127.0.0.1:$PORT" --release-id gpu-session --c1-a "$C1/lora_a_val.jsonl" --c1-b "$C1/lora_b_val.jsonl" \
  --n-a "$N_A" --n-b "$N_B" --concurrency "$CONC" --token-file "$RUN/token" --put-release --put-timeout 900 --max-seconds "$left" --out "$RUN/measure.json"
MRC=$?
echo "$(date +%H:%M:%S) 측정 후 $(gpu_mem)" >> "$RUN/logs/gpu_mem.log"

# 7) 요약(서버 종료는 trap)
log "요약: 결과 $RUN/measure.json, 표 $RUN/measure.txt (종료 코드 $MRC)"
log "GPU 메모리 기록 $(wc -l < "$RUN/logs/gpu_mem.log")줄($RUN/logs/gpu_mem.log). 서버 로그의 KV 캐시 줄: $(grep -a -i -m2 'KV cache' "$RUN/logs/server.log" | tr '\n' ' ' | cut -c1-300)"
exit "$MRC"
