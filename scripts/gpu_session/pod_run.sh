#!/usr/bin/env bash
# [PC에서] 한 명령: 키트 전송 -> 파드 사전점검(+설치) -> 세션 실행 -> 결과 회수. 임대 시간을 줄이려는 자동화다.
# 사용: pod_run.sh --ssh "-p PORT root@HOST" --adapter-a DIR --adapter-b DIR --c1-dir DIR --i-confirm-upload-data [--n-a 20 --n-b 20 --concurrency 1] [--base REMOTE_DIR(RunPod: /workspace/dabin; 기본 파드의 $HOME/dabin)] [--skip-setup] [--out DIR]
# 데이터 반출: 어댑터 2개(약 350MB)와 C1 val jsonl 2개(환자 발화 합성 데이터)가 외부 파드로 올라간다. 사용자가 결정할 일이라
#   --i-confirm-upload-data 가 없으면 아무것도 보내지 않고 목록만 출력한다. 비밀(토큰·키·.env·웹훅)은 보내지 않는다(키트는 코드만).
# 리포는 GitHub 없이 쓴다: 키트(tar.gz)에 필요한 코드가 모두 들어 있다(make_kit.sh).
# 모델 프로파일: 24GB GPU(4090급)면 rtx4090-24g.json(util 0.9, 파드 GPU는 단독 사용이라 preflight 여유 0). 다른 GPU면 --profile.
set -u -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
BASE='$HOME/dabin'; SSHOPT=""; AD_A=""; AD_B=""; C1=""; OK=0; SETUP=1; OUT="$HOME/pod-results"; PROFILE="rtx4090-24g.json"; EXTRA=(); NA=20; NB=20; CONC=1; BUDGET=45
PYC="$ROOT/.venv/bin/python"; [ -x "$PYC" ] || PYC=python3
while [ $# -gt 0 ]; do case "$1" in
  --ssh) SSHOPT="$2"; shift 2;; --adapter-a) AD_A="$2"; shift 2;; --adapter-b) AD_B="$2"; shift 2;; --c1-dir) C1="$2"; shift 2;;
  --i-confirm-upload-data) OK=1; shift;; --skip-setup) SETUP=0; shift;; --base) BASE="$2"; shift 2;; --out) OUT="$2"; shift 2;; --profile) PROFILE="$2"; shift 2;;
  --n-a) NA="$2"; shift 2;; --n-b) NB="$2"; shift 2;; --concurrency) CONC="$2"; shift 2;; --budget-min) BUDGET="$2"; shift 2;;
  --python-local) PYC="$2"; shift 2;; *) EXTRA+=("$1"); shift;; esac; done
[ -n "$SSHOPT" ] && [ -n "$AD_A" ] && [ -n "$AD_B" ] && [ -n "$C1" ] || { sed -n 2,9p "$0"; exit 64; }
for f in "$AD_A/adapter_config.json" "$AD_A/adapter_model.safetensors" "$AD_B/adapter_config.json" "$AD_B/adapter_model.safetensors" "$C1/lora_a_val.jsonl" "$C1/lora_b_val.jsonl"; do [ -f "$f" ] || { echo "없다: $f" >&2; exit 64; }; done
echo "보낼 것: 키트 코드(tar.gz) / 어댑터 $AD_A, $AD_B ($(du -shc "$AD_A" "$AD_B" | tail -1 | cut -f1)) / $C1/lora_a_val.jsonl, lora_b_val.jsonl ($(wc -l < "$C1/lora_a_val.jsonl")+$(wc -l < "$C1/lora_b_val.jsonl")행)"
[ "$OK" -eq 1 ] || { echo "위 데이터를 외부 파드로 올리려면 사용자 결정이 필요하다. 결정했으면 --i-confirm-upload-data 를 붙인다. 아무것도 보내지 않았다."; exit 3; }
S() { ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 $SSHOPT "$@"; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
step() { echo "[pod_run $(date +%H:%M:%S)] $*"; }
# 키트: boundary-contracts 2.0.2(deploy 기준)가 설치된 파이썬으로 만든다. 다른 버전이면 make_kit.sh가 거부한다
bash "$HERE/make_kit.sh" "$T/mr-kit.tgz" --python "$PYC" >/dev/null || { echo "키트 생성 실패" >&2; exit 1; }
step "파드 연결과 전송"
S "mkdir -p $BASE" || { echo "SSH 접속 실패" >&2; exit 1; }
# 전송은 ssh 표준입력으로 한다(scp의 포트 옵션 차이를 피하고, 원격의 $BASE를 원격 셸이 해석하게 한다). 큰 파일이 container disk를 거치지 않고 바로 $BASE(볼륨)에 쓰인다
mkdir -p "$T/up/adapters/LORA_A" "$T/up/adapters/LORA_B" "$T/up/c1"
cp "$AD_A/adapter_config.json" "$AD_A/adapter_model.safetensors" "$T/up/adapters/LORA_A/"; cp "$AD_B/adapter_config.json" "$AD_B/adapter_model.safetensors" "$T/up/adapters/LORA_B/"
cp "$C1/lora_a_val.jsonl" "$C1/lora_b_val.jsonl" "$T/up/c1/"
S "cd $BASE && rm -rf mr-kit && tar -xzf -" < "$T/mr-kit.tgz" || { echo "키트 전송 실패" >&2; exit 1; }
tar -C "$T/up" -czf - . | S "cd $BASE && tar -xzf -" || { echo "데이터 전송 실패" >&2; exit 1; }
if [ "$SETUP" -eq 1 ]; then step "파드 설정(없으면 설치)"; S "bash $BASE/mr-kit/scripts/gpu_session/pod_setup.sh --base $BASE" || { echo "파드 설정 실패: 임대를 끝내고 원인을 본다" >&2; exit 2; }; fi
step "세션 실행(행 수 A=$NA B=$NB, 동시 $CONC)"
S "bash $BASE/mr-kit/scripts/gpu_session/run_session.sh --base $BASE --python $BASE/venv/bin/python --adapter-a $BASE/adapters/LORA_A --adapter-b $BASE/adapters/LORA_B --c1-dir $BASE/c1 --profile $BASE/mr-kit/profiles/$PROFILE --margin-mib 0 --budget-min $BUDGET --n-a $NA --n-b $NB --concurrency $CONC ${EXTRA[*]:-}"
RC=$?
step "결과 회수(종료 코드 $RC)"
mkdir -p "$OUT"
# 회수에서 제외: 번들(어댑터 복사본), 토큰, 인증 헤더 파일. --exclude는 tar 인자 앞에 둔다
S "cd $BASE/mr-session && R=\$(ls -td run-* | head -1) && tar -czf - --exclude=\$R/release --exclude=\$R/bundles --exclude=\$R/tokens --exclude=\$R/token --exclude=\$R/auth_header \$R" > "$OUT/session-$(date +%Y%m%d-%H%M%S).tgz" && [ -s "$(ls -t "$OUT"/session-*.tgz | head -1)" ] && echo "저장: $OUT" || echo "결과 회수 실패: 파드의 $BASE/mr-session 이 남아 있다. 임대를 끝내기 전에 수동으로 가져온다" >&2
echo "임대를 끝내도 되는지: 결과 파일이 위에 저장됐고(tgz 크기 > 0) 종료 코드가 0/4인지 확인한 뒤 파드를 중지한다."
exit "$RC"
