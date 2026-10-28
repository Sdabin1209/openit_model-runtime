#!/usr/bin/env bash
# [임대 GPU 파드에서] 빈 Ubuntu+CUDA 파드를 세션 가능 상태로 만든다. 한 번만, 위에서부터 첫 실패에서 멈춘다(set -e).
# 왜 멈추나: 임대는 시간당 과금이다. 환경이 안 맞는데 계속 진행해 봐야 비용만 나간다. 각 단계 앞에 '무엇을 보고 진행/중단'을 둔다.
# 사용: bash pod_setup.sh [--venv DIR(기본 ~/dabin/venv)] [--model Qwen/Qwen3-8B] [--min-disk-gb 80]
# 하지 않는 것: sudo, 시스템 패키지 설치, /etc 변경. 파이썬 venv는 홈 아래에만 만든다.
set -eu
VENV="$HOME/dabin/venv"; MODEL="Qwen/Qwen3-8B"; MIN_GB=80; KIT="$HOME/dabin/mr-kit"
while [ $# -gt 0 ]; do case "$1" in --venv) VENV="$2"; shift 2;; --model) MODEL="$2"; shift 2;; --min-disk-gb) MIN_GB="$2"; shift 2;; *) echo "알 수 없는 인자 $1" >&2; exit 64;; esac; done
step() { echo "[pod_setup $(date +%H:%M:%S)] $*"; }
stop() { echo "[pod_setup] 중단: $*" >&2; exit 1; }
mkdir -p "$HOME/dabin"
# 1) GPU와 드라이버. 잠금 파일의 torch는 CUDA 13 휠(nvidia-*-cu13)이라 드라이버가 CUDA 13을 지원해야 한다(gpu-dev 드라이버 591.86/CUDA 13.1에서 동작 확인).
command -v nvidia-smi >/dev/null || stop "nvidia-smi가 없다(GPU 파드가 아니다)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
CUDAV="$(nvidia-smi | grep -o 'CUDA Version: [0-9]*' | grep -o '[0-9]*$' || true)"
[ -n "$CUDAV" ] && [ "$CUDAV" -ge 13 ] || stop "드라이버가 CUDA 13 미만($CUDAV)이다. 잠금 파일의 torch(cu13)가 안 뜬다. CUDA 13 지원 템플릿으로 파드를 다시 만든다"
# 2) 디스크: 모델(Qwen3-8B 약 16GB) + 어댑터 + venv(torch/vllm 수 GB) + 캐시. 모자라면 시작 전에 멈춘다
AVAIL_GB=$(df -P --block-size=1G "$HOME" | awk 'NR==2{print $4}')
[ "$AVAIL_GB" -ge "$MIN_GB" ] || stop "홈의 남은 디스크 ${AVAIL_GB}GB < ${MIN_GB}GB. 파드 디스크(볼륨)를 늘린다"
# 3) 인터넷(PyPI, Hugging Face) 확인. 없으면 아무것도 설치하지 않는다
curl -sS -m 10 -o /dev/null -I https://pypi.org/simple/ || stop "PyPI에 닿지 못한다"
curl -sS -m 10 -o /dev/null -I https://huggingface.co || stop "Hugging Face에 닿지 못한다"
# 4) 파이썬 3.12 venv (잠금 파일이 3.12용인지는 gpu-dev 환경 기준. 다르면 중단)
PYBIN="$(command -v python3.12 || true)"; [ -n "$PYBIN" ] || stop "python3.12가 없다. 'uv python install 3.12' 또는 python3.12가 있는 템플릿을 쓴다(sudo 없이 uv로 가능)"
[ -x "$VENV/bin/python" ] || "$PYBIN" -m venv "$VENV" || stop "venv를 만들지 못했다(python3.12-venv 없음이면 uv venv --python 3.12 $VENV 를 쓴다)"
step "잠금 파일 설치(수 GB, 수 분)"
[ -f "$KIT/requirements-gpu.lock" ] || stop "$KIT/requirements-gpu.lock 이 없다(키트에 포함되어야 한다)"
"$VENV/bin/pip" install -q --no-deps -r "$KIT/requirements-gpu.lock" || stop "pip 설치 실패"
"$VENV/bin/python" -c "import vllm,jsonschema,torch;print('vllm',vllm.__version__,'torch',torch.__version__,'cuda_available',torch.cuda.is_available())"
"$VENV/bin/python" -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" || stop "torch가 GPU를 못 본다"
[ "$("$VENV/bin/python" -c 'import vllm;print(vllm.__version__)')" = "0.31.0" ] || stop "vllm 버전이 키트 기준(0.31.0)과 다르다"
# 5) 기반 모델 내려받기(공개 모델이라 토큰 불필요). 이후 run_session은 HF_HUB_OFFLINE=1로 읽기만 한다
step "기반 모델 내려받기: $MODEL"
"$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s;print(s('$MODEL'))" || stop "모델 내려받기 실패"
step "완료. 다음: bash $KIT/scripts/gpu_session/pod_run.sh (또는 run_session.sh --python $VENV/bin/python ...)"
