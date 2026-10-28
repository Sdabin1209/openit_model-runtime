#!/usr/bin/env bash
# [임대 GPU 파드에서] 빈 Ubuntu+CUDA 파드를 세션 가능 상태로 만든다. 한 번만, 위에서부터 첫 실패에서 멈춘다(set -e).
# 왜 멈추나: 임대는 시간당 과금이다. 환경이 안 맞는데 계속 진행해 봐야 비용만 나간다. 각 단계 앞에 '무엇을 보고 진행/중단'을 둔다.
# 사용: bash pod_setup.sh [--base DIR(기본 $HOME/dabin; RunPod는 볼륨인 /workspace/dabin)] [--model Qwen/Qwen3-8B] [--min-disk-gb 40]
# 모든 설치·캐시(venv, 키트, HF 모델, pip 캐시·임시 파일, 작업 폴더)는 --base 한 곳 아래에만 둔다. 디스크 검사도 그 폴더가 있는 파일시스템으로 한다
#   (RunPod에서 $HOME은 작은 container disk라, 큰 것이 거기 쌓이면 볼륨이 남아도 가득 찬다).
# 하지 않는 것: sudo, 시스템 패키지 설치, /etc 변경. 모든 쓰기는 --base 아래에만 한다. sudo 없이, root(RunPod 기본)로도 일반 사용자로도 같게 동작한다.
set -eu
BASE="$HOME/dabin"; MODEL="Qwen/Qwen3-8B"; MIN_GB=40
while [ $# -gt 0 ]; do case "$1" in --base) BASE="$2"; shift 2;; --model) MODEL="$2"; shift 2;; --min-disk-gb) MIN_GB="$2"; shift 2;; *) echo "알 수 없는 인자 $1" >&2; exit 64;; esac; done
step() { echo "[pod_setup $(date +%H:%M:%S)] $*"; }
stop() { echo "[pod_setup] 중단: $*" >&2; exit 1; }
mkdir -p "$BASE" || { echo "[pod_setup] 중단: $BASE 를 만들 수 없다" >&2; exit 1; }
BASE="$(cd "$BASE" && pwd -P)"; VENV="$BASE/venv"; KIT="$BASE/mr-kit"; mkdir -p "$BASE/tmp" "$BASE/pip-cache" "$BASE/hf"
# 큰 임시·캐시 파일이 container disk로 새지 않게 모두 BASE 아래로 보낸다(pip는 휠을 TMPDIR에 받는다)
export TMPDIR="$BASE/tmp" PIP_CACHE_DIR="$BASE/pip-cache" HF_HOME="$BASE/hf"
# 1) GPU와 드라이버. 잠금 파일의 torch는 CUDA 13 휠(nvidia-*-cu13)이라 드라이버가 CUDA 13을 지원해야 한다(gpu-dev 드라이버 591.86/CUDA 13.1에서 동작 확인).
command -v nvidia-smi >/dev/null || stop "nvidia-smi가 없다(GPU 파드가 아니다)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
CUDAV="$(nvidia-smi | grep -o 'CUDA Version: [0-9]*' | grep -o '[0-9]*$' || true)"
[ -n "$CUDAV" ] && [ "$CUDAV" -ge 13 ] || stop "드라이버가 CUDA 13 미만($CUDAV)이다. 잠금 파일의 torch(cu13)가 안 뜬다. CUDA 13 지원 템플릿으로 파드를 다시 만든다"
# 2) 디스크(BASE가 있는 파일시스템). 기본 40GB의 근거(docs/GPU_SESSION.md): 모델 16.4(추정: 8.2B x 2바이트) + venv 8.3(gpu-dev 측정) + 설치 중 휠 임시 파일 최대 8.3(venv와 같다고 본 추정)
#   + 어댑터 0.35 + 실행 캐시 2(측정: 1.5B에서 52MB, 8B는 추정 상한) = 약 35.4 -> 40으로 올림. 50GB 볼륨에서 통과해야 한다.
#   [수정] gpu-dev에서 pod_setup.sh를 실제 실행해 잰 값: venv 8.3GB, pip 휠 3.7GB(--no-cache-dir로 설치 뒤 남기지 않음, 설치 중 임시로는 필요), 설치 2분 12초.
#   그래서 합계 = 16.4(추정) + 8.3(측정) + 3.7(측정) + 0.35 + 2(추정) = 30.8GB, 여유 약 30%를 얹어 40.
AVAIL_GB=$(df -P --block-size=1G "$BASE" | awk 'NR==2{print $4}')
[ "$AVAIL_GB" -ge "$MIN_GB" ] || stop "$BASE 의 파일시스템 남은 디스크 ${AVAIL_GB}GB < ${MIN_GB}GB. 볼륨을 늘리거나 --base를 큰 볼륨(RunPod: /workspace/dabin)으로 준다"
# 3) 인터넷(PyPI, Hugging Face) 확인. 없으면 아무것도 설치하지 않는다
curl -sS -m 10 -o /dev/null -I https://pypi.org/simple/ || stop "PyPI에 닿지 못한다"
curl -sS -m 10 -o /dev/null -I https://huggingface.co || stop "Hugging Face에 닿지 못한다"
# 4) 파이썬 3.12 venv (잠금 파일이 3.12용인지는 gpu-dev 환경 기준. 다르면 중단)
PYBIN="$(command -v python3.12 || true)"; [ -n "$PYBIN" ] || stop "python3.12가 없다. 'uv python install 3.12' 또는 python3.12가 있는 템플릿을 쓴다(sudo 없이 uv로 가능)"
[ -x "$VENV/bin/python" ] || "$PYBIN" -m venv "$VENV" || stop "venv를 만들지 못했다(python3.12-venv 없음이면 uv venv --python 3.12 $VENV 를 쓴다)"
# 4b) Triton(vLLM의 LoRA·샘플링 커널)은 첫 실행 때 C 컴파일러와 Python.h로 런처를 빌드한다. 없으면 설치·모델 내려받기를 다 마친 뒤 서버 기동에서야 실패하므로(임대 시간) 여기서 먼저 막는다.
#   표준 요건: Triton 문서/소스(runtime/build.py)가 cc(gcc)와 Python 헤더를 쓴다. PC에서는 '있음' 경로(gcc, Python.h 존재)만 확인했다. '없음' 중단 경로와 GPU 행렬곱은 파드에서만 실행된다.
{ command -v gcc >/dev/null || command -v cc >/dev/null; } || stop "C 컴파일러(gcc/cc)가 없다. Triton이 커널 런처를 빌드하지 못한다. gcc가 있는 템플릿을 쓴다(RunPod pytorch 템플릿은 보통 있다)"
PYINC="$("$VENV/bin/python" -c 'import sysconfig;print(sysconfig.get_paths()["include"])')"
[ -f "$PYINC/Python.h" ] || stop "Python.h가 없다($PYINC). Triton이 빌드하지 못한다. python3.12-dev가 있는 템플릿을 쓰거나 'uv venv --python 3.12'(독립 파이썬은 헤더를 포함)로 venv를 만든다"
step "잠금 파일 설치(수 GB, 수 분)"
[ -f "$KIT/requirements-gpu.lock" ] || stop "$KIT/requirements-gpu.lock 이 없다(키트에 포함되어야 한다)"
"$VENV/bin/pip" install -q --no-cache-dir --no-deps -r "$KIT/requirements-gpu.lock" || stop "pip 설치 실패"
"$VENV/bin/python" -c "import vllm,jsonschema,torch;print('vllm',vllm.__version__,'torch',torch.__version__,'cuda_available',torch.cuda.is_available())"
"$VENV/bin/python" -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" || stop "torch가 GPU를 못 본다"
# GPU에서 실제 커널 하나가 도는지(이 GPU 아키텍처용 코드가 휠에 있는지). 수 초. 실패하면 vLLM도 못 뜬다
"$VENV/bin/python" -c "import torch;x=torch.randn(256,256,device='cuda',dtype=torch.bfloat16);print('gpu matmul ok',(x@x).float().abs().mean().item()>0, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))" || stop "GPU에서 bf16 행렬곱이 실패했다(드라이버·아키텍처 불일치)"
[ "$("$VENV/bin/python" -c 'import vllm;print(vllm.__version__)')" = "0.31.0" ] || stop "vllm 버전이 키트 기준(0.31.0)과 다르다"
# 5) 기반 모델 내려받기(공개 모델이라 토큰 불필요). 이후 run_session은 HF_HUB_OFFLINE=1로 읽기만 한다
step "기반 모델 내려받기: $MODEL"
"$VENV/bin/python" -c "from huggingface_hub import snapshot_download as s;print(s('$MODEL'))" || stop "모델 내려받기 실패"
step "완료. 다음: PC에서 pod_run.sh --base $BASE ... (파드 안에서는 run_session.sh --base $BASE ...)"
