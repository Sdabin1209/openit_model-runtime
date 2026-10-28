#!/usr/bin/env bash
# [임대 GPU 파드에서] 빈 Ubuntu+CUDA 파드를 세션 가능 상태로 만든다. 한 번만, 위에서부터 첫 실패에서 멈춘다(set -e).
# 왜 멈추나: 임대는 시간당 과금이다. 환경이 안 맞는데 계속 진행해 봐야 비용만 나간다. 각 단계 앞에 '무엇을 보고 진행/중단'을 둔다.
# 파드 이미지가 조금 달라도(파이썬 버전, gcc, 헤더 유무) 키트가 돈다: 없는 것은 스스로 준비한다(ADR 0014).
#   - 파이썬 3.12: 항상 uv가 받는 독립 파이썬(python-build-standalone)을 쓴다. 헤더(Python.h)와 venv 모듈이 들어 있어 시스템 파이썬 상태에 기대지 않는다.
#   - uv: 버전과 sha256을 이 파일에 고정해 GitHub 릴리스 tarball을 받아 검증한 뒤 --base 아래에 푼다. 루트 불필요.
#   - gcc, libc6-dev(Triton 런처 빌드용), curl, ca-certificates: 없으면 root일 때만 apt-get으로 설치한다. root가 아니면 무엇이 없는지 말하고 중단한다. sudo는 쓰지 않는다.
# 사용: bash pod_setup.sh [--base DIR(기본 $HOME/dabin; RunPod는 볼륨인 /workspace/dabin)] [--profile rtx4090-24g.json|경로] [--model 프로파일의 base_model과 같은 값만] [--min-disk-gb 40]
#                        [--stage all|gpu|prereq|install|verify|model] [--lock FILE(기본 $BASE/mr-kit/requirements-gpu.lock)]
# 모델 이름의 출처는 프로파일의 base_model 하나다(ADR 0015). --model은 받는 모델을 고르는 옵션이 아니라 '프로파일과 같은지' 확인용이고, 다르면 시작 전에 중단한다.
#   왜: 받은 모델과 서버가 읽을 모델이 어긋나면 run_session의 HF_HUB_OFFLINE=1에서 LocalEntryNotFoundError로 기동이 실패하고, 그때는 이미 파드 시간을 쓴 뒤다.
# 단계(--stage, 기본 all = gpu -> prereq -> install -> verify -> model 순서로 전부):
#   gpu     nvidia-smi와 드라이버 CUDA >= 13 점검 (GPU가 있어야만 확인 가능)
#   prereq  디스크, 인터넷, gcc/헤더/curl 확보, uv와 파이썬 3.12 확보, venv 생성, Python.h로 C 컴파일 시험 (GPU 무관)
#   install 잠금 파일(또는 --lock)을 venv에 설치 (GPU 무관)
#   verify  import, torch GPU, bf16 행렬곱, vllm==0.31.0 (GPU 필요)
#   model   기반 모델 내려받기 (인터넷)
# 왜 단계로 나누나: GPU 없는 곳(PC, CPU 컨테이너)에서 '준비' 부분만 실제로 돌려 볼 수 있어야 하고, 실패한 단계만 다시 돌릴 수 있어야 한다.
#   단계 사이는 파일시스템(--base 아래의 uv, venv)으로만 이어진다. 환경 변수나 GPU 유무로 분기하지 않는다: 같은 코드가 같은 순서로 돈다.
# 모든 설치·캐시(uv, 파이썬, venv, 키트, HF 모델, 캐시·임시 파일, 작업 폴더)는 --base 한 곳 아래에만 둔다. 디스크 검사도 그 폴더가 있는 파일시스템으로 한다
#   (RunPod에서 $HOME은 작은 container disk라, 큰 것이 거기 쌓이면 볼륨이 남아도 가득 찬다).
# 하지 않는 것: sudo, /etc 변경. 시스템 쓰기는 root일 때 apt-get으로 gcc 등을 설치하는 것뿐이고 그 밖의 모든 쓰기는 --base 아래다.
set -eu
BASE="$HOME/dabin"; MODEL_ARG=""; PROFILE="rtx4090-24g.json"; MIN_GB=40; STAGE=all; LOCK=""
while [ $# -gt 0 ]; do case "$1" in
  --base) BASE="$2"; shift 2;; --model) MODEL_ARG="$2"; shift 2;; --profile) PROFILE="$2"; shift 2;; --min-disk-gb) MIN_GB="$2"; shift 2;;
  --stage) STAGE="$2"; shift 2;; --lock) LOCK="$2"; shift 2;;
  *) echo "알 수 없는 인자 $1" >&2; exit 64;; esac; done
case "$STAGE" in all|gpu|prereq|install|verify|model) ;; *) echo "알 수 없는 --stage $STAGE (all|gpu|prereq|install|verify|model)" >&2; exit 64;; esac
# 프로파일 해석: 이름만 주면 키트의 profiles/ 아래, 경로(/ 포함)면 그 파일. 기본값은 pod_run.sh와 docs/GPU_SESSION.md가 쓰는 rtx4090-24g.json
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$PROFILE" in */*) PROFILE_FILE="$PROFILE";; *) PROFILE_FILE="$HERE/../../profiles/$PROFILE";; esac
[ -f "$PROFILE_FILE" ] || { echo "[pod_setup] 중단: 프로파일이 없다: $PROFILE_FILE" >&2; exit 64; }
# JSON 파서 없이 읽는다(prereq 전에는 venv도 없고 시스템 파이썬을 가정하지 않는다): 프로파일의 "base_model" 한 줄
MODEL="$(sed -n 's/^[[:space:]]*"base_model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PROFILE_FILE" | head -1)"
[ -n "$MODEL" ] || { echo "[pod_setup] 중단: $PROFILE_FILE 에 base_model이 없다" >&2; exit 64; }
if [ -n "$MODEL_ARG" ] && [ "$MODEL_ARG" != "$MODEL" ]; then
  echo "[pod_setup] 중단: --model $MODEL_ARG 이(가) 프로파일 $PROFILE 의 base_model($MODEL)과 다르다. 받은 모델과 서버가 읽을 모델이 어긋나면 오프라인 기동이 LocalEntryNotFoundError로 실패한다. 다른 모델이 필요하면 프로파일을 바꾸거나 맞는 프로파일을 --profile로 준다" >&2; exit 64
fi
step() { echo "[pod_setup $(date +%H:%M:%S)] $*"; }
stop() { echo "[pod_setup] 중단: $*" >&2; exit 1; }
want() { [ "$STAGE" = all ] || [ "$STAGE" = "$1" ]; }
mkdir -p "$BASE" || { echo "[pod_setup] 중단: $BASE 를 만들 수 없다" >&2; exit 1; }
BASE="$(cd "$BASE" && pwd -P)"; VENV="$BASE/venv"; KIT="$BASE/mr-kit"; UVDIR="$BASE/uv"; UV="$UVDIR/uv"
[ -n "$LOCK" ] || LOCK="$KIT/requirements-gpu.lock"
mkdir -p "$BASE/tmp" "$BASE/uv-cache" "$BASE/python" "$BASE/hf"
# 큰 임시·캐시 파일이 container disk로 새지 않게 모두 BASE 아래로 보낸다(uv는 휠을 TMPDIR에 받는다)
export TMPDIR="$BASE/tmp" UV_CACHE_DIR="$BASE/uv-cache" UV_PYTHON_INSTALL_DIR="$BASE/python" HF_HOME="$BASE/hf"
# uv가 받은 파이썬만 쓴다(시스템 파이썬에 기대지 않는다). 기본값 'managed'는 시스템 파이썬을 먼저 고르므로 명시한다
export UV_PYTHON_PREFERENCE=only-managed UV_NO_PROGRESS=1
# uv 버전과 sha256 고정(ADR 0014). 출처: https://github.com/astral-sh/uv/releases/tag/$UV_VERSION 의 uv-<arch>-unknown-linux-gnu.tar.gz.sha256 (2026-10-28 내려받아 기록)
UV_VERSION="0.13.6"
UV_SHA256_x86_64="f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645"
UV_SHA256_aarch64="229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d"
PYVER="3.12"   # 잠금 파일(requirements-gpu.lock)이 cp312 휠 기준

# ---- gpu ----
if want gpu; then
# 잠금 파일의 torch는 CUDA 13 휠(nvidia-*-cu13)이라 드라이버가 CUDA 13을 지원해야 한다(gpu-dev 드라이버 591.86/CUDA 13.1에서 동작 확인).
command -v nvidia-smi >/dev/null || stop "nvidia-smi가 없다(GPU 파드가 아니다)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
CUDAV="$(nvidia-smi | grep -o 'CUDA Version: [0-9]*' | grep -o '[0-9]*$' || true)"
[ -n "$CUDAV" ] && [ "$CUDAV" -ge 13 ] || stop "드라이버가 CUDA 13 미만($CUDAV)이다. 잠금 파일의 torch(cu13)가 안 뜬다. CUDA 13 지원 템플릿으로 파드를 다시 만든다"
fi

# ---- prereq ----
if want prereq; then
# 디스크(BASE가 있는 파일시스템). 기본 40GB의 근거(docs/GPU_SESSION.md): 모델 16.4(추정: 8.2B x 2바이트) + venv 8.3(gpu-dev 측정) + 설치 중 휠 임시 파일 최대 8.3(venv와 같다고 본 추정)
#   + 어댑터 0.35 + 실행 캐시 2(측정: 1.5B에서 52MB, 8B는 추정 상한) = 약 35.4 -> 40으로 올림. 50GB 볼륨에서 통과해야 한다.
#   [수정] gpu-dev에서 pod_setup.sh를 실제 실행해 잰 값: venv 8.3GB, 휠 3.7GB(캐시에 남기지 않음, 설치 중 임시로는 필요), 설치 2분 12초(pip 기준; uv로 바꾼 뒤 시간은 측정 안 함).
#   그래서 합계 = 16.4(추정) + 8.3(측정) + 3.7(측정) + 0.35 + 2(추정) = 30.8GB, 여유 약 30%를 얹어 40. uv 바이너리(수십 MB)와 독립 파이썬(수백 MB 이하, 측정 안 함)은 이 여유에 든다고 본다.
AVAIL_GB=$(df -P --block-size=1G "$BASE" | awk 'NR==2{print $4}')
[ "$AVAIL_GB" -ge "$MIN_GB" ] || stop "$BASE 의 파일시스템 남은 디스크 ${AVAIL_GB}GB < ${MIN_GB}GB. 볼륨을 늘리거나 --base를 큰 볼륨(RunPod: /workspace/dabin)으로 준다"

# 시스템 도구: gcc(Triton 런처 빌드), libc6-dev(libc 헤더), curl·ca-certificates(uv와 파이썬 내려받기). 없을 때만, root일 때만 apt-get으로 설치한다.
# 파이썬 헤더는 uv가 받는 독립 파이썬에 들어 있으므로 python3.12-dev는 필요 없다(아래 Python.h 시험이 확인한다).
MISSING=""
{ command -v gcc >/dev/null || command -v cc >/dev/null; } || MISSING="$MISSING gcc"
[ -e /usr/include/stdio.h ] || [ -e "/usr/include/$(uname -m)-linux-gnu/bits/libc-header-start.h" ] || MISSING="$MISSING libc6-dev"
command -v curl >/dev/null || MISSING="$MISSING curl"
[ -e /etc/ssl/certs/ca-certificates.crt ] || MISSING="$MISSING ca-certificates"
if [ -n "$MISSING" ]; then
  [ "$(id -u)" -eq 0 ] || stop "없는 것:$MISSING. root가 아니라 설치할 수 없다(sudo는 쓰지 않는다). 관리자가 'apt-get install -y$MISSING'을 하거나 root 파드를 쓴다"
  command -v apt-get >/dev/null || stop "없는 것:$MISSING. apt-get이 없는 배포판이라 자동 설치하지 못한다. 직접 설치한다"
  step "apt-get으로 설치:$MISSING"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends $MISSING >/dev/null || stop "apt-get 설치 실패:$MISSING"
fi

# 인터넷 확인. 없으면 아무것도 내려받지 않는다. github.com은 uv와 독립 파이썬(python-build-standalone) 출처다
curl -sS -m 10 -o /dev/null -I https://pypi.org/simple/ || stop "PyPI에 닿지 못한다"
curl -sS -m 10 -o /dev/null -I https://github.com || stop "github.com에 닿지 못한다(uv와 독립 파이썬 출처)"
curl -sS -m 10 -o /dev/null -I https://huggingface.co || stop "Hugging Face에 닿지 못한다"

# uv: 고정 버전이 이미 있으면 재사용(멱등), 아니면 내려받아 sha256을 확인한 뒤에만 푼다
if [ "$("$UV" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]; then
  ARCH="$(uname -m)"
  case "$ARCH" in x86_64) EXPECT="$UV_SHA256_x86_64";; aarch64|arm64) ARCH=aarch64; EXPECT="$UV_SHA256_aarch64";; *) stop "uv 고정 해시가 없는 CPU($ARCH). x86_64와 aarch64만 지원한다";; esac
  step "uv $UV_VERSION 내려받기($ARCH)"
  TARBALL="$BASE/tmp/uv-$ARCH.tar.gz"
  curl -fsSL -m 120 -o "$TARBALL" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$ARCH-unknown-linux-gnu.tar.gz" || stop "uv 내려받기 실패"
  [ "$(sha256sum "$TARBALL" | awk '{print $1}')" = "$EXPECT" ] || stop "uv tarball sha256이 고정값과 다르다(변조 또는 잘못된 파일). 풀지 않는다"
  rm -rf "$BASE/tmp/uv-x" && mkdir -p "$BASE/tmp/uv-x" "$UVDIR"
  tar -xzf "$TARBALL" -C "$BASE/tmp/uv-x" --strip-components=1 || stop "uv 풀기 실패"
  mv "$BASE/tmp/uv-x/uv" "$UV" && chmod 755 "$UV"; rm -rf "$BASE/tmp/uv-x" "$TARBALL"
fi
step "uv $("$UV" --version | awk '{print $2}')로 파이썬 $PYVER 확보"
"$UV" python install "$PYVER" || stop "uv가 파이썬 $PYVER 를 받지 못했다"
if [ -x "$VENV/bin/python" ]; then
  [ "$("$VENV/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])')" = "$PYVER" ] || stop "기존 $VENV 가 파이썬 $PYVER 가 아니다. 지우고 다시 실행한다(rm -rf $VENV)"
else
  "$UV" venv --python "$PYVER" "$VENV" || stop "venv를 만들지 못했다"
fi
# Triton(vLLM의 LoRA·샘플링 커널)은 첫 실행 때 C 컴파일러와 Python.h로 런처를 빌드한다. 없으면 설치·모델 내려받기를 다 마친 뒤 서버 기동에서야 실패하므로(임대 시간)
#   여기서 파일 유무가 아니라 '실제로 컴파일되는지'로 먼저 막는다. 표준 요건: Triton 소스(runtime/build.py)가 cc와 Python 헤더를 쓴다.
PYINC="$("$VENV/bin/python" -c 'import sysconfig;print(sysconfig.get_paths()["include"])')"
printf '#include <Python.h>\nint main(void){return 0;}\n' > "$BASE/tmp/hdr_check.c"
"${CC:-$(command -v gcc || command -v cc)}" -I"$PYINC" -c -o "$BASE/tmp/hdr_check.o" "$BASE/tmp/hdr_check.c" || stop "Python.h를 포함한 C 파일이 컴파일되지 않는다($PYINC). Triton이 런처를 빌드하지 못한다"
rm -f "$BASE/tmp/hdr_check.c" "$BASE/tmp/hdr_check.o"
step "prereq 통과: python $("$VENV/bin/python" -c 'import sys;print(sys.version.split()[0])'), $(gcc --version 2>/dev/null | head -1 || echo cc)"
fi

# ---- install ----
if want install; then
[ -x "$UV" ] && [ -x "$VENV/bin/python" ] || stop "uv 또는 venv가 없다. 먼저 --stage prereq"
step "잠금 파일 설치(수 GB, 수 분): $LOCK"
[ -f "$LOCK" ] || stop "$LOCK 이 없다(키트에 포함되어야 한다)"
# --no-deps: 잠금 파일이 전이 의존까지 모두 고정한 것이라 해석을 다시 하지 않는다. --no-cache: 3.7GB 휠을 설치 뒤 남기지 않는다
"$UV" pip install -q --no-cache --no-deps --python "$VENV/bin/python" -r "$LOCK" || stop "uv pip 설치 실패"
fi

# ---- verify ----
if want verify; then
[ -x "$VENV/bin/python" ] || stop "venv가 없다. 먼저 --stage prereq 와 install"
"$VENV/bin/python" -c "import vllm,jsonschema,torch;print('vllm',vllm.__version__,'torch',torch.__version__,'cuda_available',torch.cuda.is_available())"
"$VENV/bin/python" -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" || stop "torch가 GPU를 못 본다"
# GPU에서 실제 커널 하나가 도는지(이 GPU 아키텍처용 코드가 휠에 있는지). 수 초. 실패하면 vLLM도 못 뜬다
"$VENV/bin/python" -c "import torch;x=torch.randn(256,256,device='cuda',dtype=torch.bfloat16);print('gpu matmul ok',(x@x).float().abs().mean().item()>0, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))" || stop "GPU에서 bf16 행렬곱이 실패했다(드라이버·아키텍처 불일치)"
[ "$("$VENV/bin/python" -c 'import vllm;print(vllm.__version__)')" = "0.31.0" ] || stop "vllm 버전이 키트 기준(0.31.0)과 다르다"
fi

# ---- model ----
if want model; then
[ -x "$VENV/bin/python" ] || stop "venv가 없다. 먼저 --stage prereq 와 install"
# 기반 모델 내려받기(공개 모델이라 토큰 불필요). 이후 run_session은 HF_HUB_OFFLINE=1로 읽기만 한다
step "기반 모델 내려받기: $MODEL (프로파일 $PROFILE)"
"$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s;print(s('$MODEL'))" || stop "모델 내려받기 실패"
fi
[ "$STAGE" != all ] || step "완료. 다음: PC에서 pod_run.sh --base $BASE ... (파드 안에서는 run_session.sh --base $BASE ...)"
[ "$STAGE" = all ] || step "단계 $STAGE 완료"
