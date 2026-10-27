#!/usr/bin/env bash
# GPU 세션 사전 점검(읽기 전용). 공유 GPU를 남이 쓰고 있으면 실행을 거부한다. 남의 프로세스는 죽이지도 건드리지도 않는다(nvidia-smi 조회와 ps 조회만).
#
# 사용: preflight.sh [--max-used-mib N] [--min-free-mib N] [--until HH:MM]
#   --max-used-mib N  GPU별 사용 중 메모리가 N MiB를 넘으면 거부(기본 2048 = 2GB; 환경 변수 PREFLIGHT_MAX_USED_MIB로도 준다)
#   --min-free-mib N  GPU별 남은 메모리가 N MiB 미만이면 거부(기본 0 = 검사 안 함; run_session.sh가 gpu_memory_utilization에서 계산해 넘긴다)
#   --until HH:MM     서버 시각(date) 기준 오늘 이 시각이 지났으면 거부(허락받은 사용 시간대의 끝)
# 종료 코드: 0 = 써도 된다, 2 = 거부(남이 쓰는 중, 메모리 부족, 시간 초과), 3 = 점검 자체를 못 함(nvidia-smi 없음·실패), 64 = 사용법 오류
# 시험용: NVIDIA_SMI=<명령>으로 nvidia-smi를 대체할 수 있다(가짜 명령으로 시험).
set -u
SMI="${NVIDIA_SMI:-nvidia-smi}"
MAX_USED="${PREFLIGHT_MAX_USED_MIB:-2048}"
MIN_FREE=0
UNTIL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --max-used-mib|--min-free-mib|--until) [ $# -ge 2 ] || { echo "$1 에 값이 필요하다. 사용: preflight.sh [--max-used-mib N] [--min-free-mib N] [--until HH:MM]" >&2; exit 2; } ;;
  esac
  case "$1" in
    --max-used-mib) MAX_USED="${2:-}"; shift 2 ;;
    --min-free-mib) MIN_FREE="${2:-}"; shift 2 ;;
    --until) UNTIL="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 64 ;;
  esac
done
case "$MAX_USED$MIN_FREE" in (*[!0-9]*|"") echo "--max-used-mib, --min-free-mib는 정수여야 한다" >&2; exit 64 ;; esac
if [ -n "$UNTIL" ] && ! [[ "$UNTIL" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then echo "--until은 HH:MM 형식이어야 한다: $UNTIL" >&2; exit 64; fi

refuse=0
say() { echo "[preflight] $*"; }

# 1) 시간 상한: 시각이 지났으면 GPU를 보기 전에 거부한다
if [ -n "$UNTIL" ]; then
  now_s=$(date +%s); until_s=$(date -d "today $UNTIL" +%s 2>/dev/null) || { echo "[preflight] 시각을 해석하지 못했다: $UNTIL" >&2; exit 64; }
  say "서버 시각 $(date +%H:%M), 사용 상한 $UNTIL"
  if [ "$now_s" -ge "$until_s" ]; then say "거부: 사용 상한 시각($UNTIL)이 지났다."; echo "PREFLIGHT=TIME"; exit 2; fi
  say "남은 시간 약 $(( (until_s - now_s) / 60 ))분 (run_session.sh는 이 안에 끝나도록 --budget-min을 받는다)"
fi

# 2) nvidia-smi 존재와 응답
if ! command -v "$SMI" >/dev/null 2>&1; then say "실패: nvidia-smi를 찾을 수 없다($SMI). GPU 서버에서 실행해야 한다."; echo "PREFLIGHT=NO_SMI"; exit 3; fi
if ! gpus=$("$SMI" --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits 2>&1); then
  say "실패: nvidia-smi가 오류로 끝났다: $gpus"; echo "PREFLIGHT=NO_SMI"; exit 3; fi
apps=$("$SMI" --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true)

# 3) GPU별 사용량
while IFS=, read -r idx used total; do
  idx=${idx// /}; used=${used// /}; total=${total// /}
  [ -z "$idx" ] && continue
  free=$(( total - used ))
  say "GPU $idx: 사용 ${used} MiB / 전체 ${total} MiB (남음 ${free} MiB)"
  if [ "$used" -gt "$MAX_USED" ]; then say "거부: GPU $idx 사용 중 메모리 ${used} MiB가 임계 ${MAX_USED} MiB를 넘는다."; refuse=1; fi
  if [ "$MIN_FREE" -gt 0 ] && [ "$free" -lt "$MIN_FREE" ]; then say "거부: GPU $idx 남은 메모리 ${free} MiB가 필요량 ${MIN_FREE} MiB보다 적다."; refuse=1; fi
done <<< "$gpus"

# 4) compute 프로세스 목록(누가 쓰는지). 소유자는 ps로 읽기만 한다
if [ -n "${apps//[[:space:]]/}" ]; then
  say "GPU를 쓰는 compute 프로세스:"
  while IFS=, read -r pid name mem; do
    pid=${pid// /}; [ -z "$pid" ] && continue
    owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ' || true)
    say "  pid=$pid 이름=${name# } 메모리=${mem// /}MiB 사용자=${owner:-알 수 없음}"
  done <<< "$apps"
  say "거부: compute 프로세스가 있다. 이 스크립트는 그 프로세스를 건드리지 않는다. 끝나기를 기다리거나 소유자와 조율한다."
  refuse=1
fi

if [ "$refuse" -ne 0 ]; then echo "PREFLIGHT=BUSY"; exit 2; fi
say "통과: GPU가 비어 있다(임계 ${MAX_USED} MiB 이하, compute 프로세스 없음)."
echo "PREFLIGHT=OK"
exit 0
