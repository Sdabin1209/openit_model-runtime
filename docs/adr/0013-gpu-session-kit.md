# ADR 0013: GPU 세션 키트(공유 GPU에서 짧고 안전한 실측)

## 상태
채택(0.7.4, 미실행). 키트는 가짜 백엔드로만 시험했다. **실제 GPU에서 돌려 본 적이 없다.**

## 맥락
H1~H4(OPEN_GAPS)와 boundary.md '실측 절차'는 Qwen3-8B + 학습된 LoRA A·B로 extract·respond의 p50/p95/max와 출력 형식 통과율을 요구한다. 사용할 수 있는 GPU는 학습 서버의 RTX PRO 6000 Blackwell(97.9GB) 1장이고 **다른 작업과 나눠 쓴다**(헤드 탐색 시점에 다른 작업이 약 91GB 사용 중). 서버에서는 `~/dabin` 안쪽만 쓸 수 있고, 환경(`openit-llm` conda env: vllm 0.31.0 등)은 다른 사람의 것이며, 인터넷/GitHub 접근을 가정하지 않는다. 사용 가능 시간대는 짧고 미정이다.

## 결정
1. **스크립트 한 벌(`scripts/gpu_session/`)이 preflight -> 번들 -> 서버 -> 적재 -> 측정 -> 정리를 한 번에 한다.** 사람이 순서를 손으로 잇지 않는다(실수와 시간 낭비 방지, D39).
2. **preflight가 거부하면 아무것도 시작하지 않는다.** compute 프로세스가 있거나 사용 중 메모리가 임계(기본 2GB)를 넘거나 필요한 남은 메모리(`gpu_memory_utilization` x 전체 + 여유 4GB)가 모자라면 종료 코드 2. 누가 쓰는지(pid, 이름, 메모리, 사용자)를 보여 주기만 하고 **남의 프로세스는 죽이지도 건드리지도 않는다.** 정리는 이 스크립트가 직접 띄운 프로세스 그룹(`setsid`)만 한다.
3. **환경을 수정하지 않는다. `PYTHONPATH`로 키트를 얹는다.** model-runtime 소스와 `boundary_contracts`(설치된 태그 패키지 그대로)와 문진 스키마를 `make_kit.sh`가 PC에서 tar로 묶고, 서버는 `~/dabin/mr-kit`에 풀어 쓴다. pip install·conda 변경이 없다.
4. **`gpu_memory_utilization`은 0.30**(프로파일 `gpu-shared-8b.json`, `--util`로 덮어씀).
5. **측정 중 요청 상한을 푼다**(`REQUEST_DEADLINE_S`/`QUEUE_WAIT_S` 600초). 기본 3.5초 상한을 켜 두면 느린 호출이 `ENGINE_TIMEOUT`으로 잘려 실제 지연 분포를 못 잰다. 상한이 켜진 동작은 `--production-limits`로 따로 본다.
6. **측정 도구는 이 영역 안에 둔다(`measure.py`).** deploy의 probe-serving은 읽기 전용으로만 봤고 p95·max·respond·정답 비교가 없다. 정답 비교는 C1 val의 assistant 메시지를 쓴다. 발화·출력 원문은 결과 파일에 남기지 않는다.
7. **서버 접속은 헤드가 한다.** 키트는 로컬(가짜 백엔드, 가짜 nvidia-smi)에서 시험 가능해야 하고 pytest(`tests/test_gpu_session.py`)로 고정한다.
8. **I4는 최소 읽기 서버(`release_server.py`)로 대신한다.** 추론 서버는 번들을 I4(HTTP.md)에서 받는다. boundary-contracts의 참조 서버(`conformance`)는 설치 패키지에 들어 있지 않아 서버에 따로 올려야 하므로 쓰지 않는다. 루프백 전용, 읽기 전용, 표준 라이브러리만.

## 왜 (따른 표준과 근거)
- 공통 규칙 '자기 리포지토리 안에서만 일한다'와 서버 쓰기 규칙(~/dabin만) -> 캐시·임시·번들·결과를 모두 `~/dabin/mr-session`으로 보낸다(`VLLM_CACHE_ROOT`, `XDG_CACHE_HOME`, `TORCHINDUCTOR_CACHE_DIR`, `TRITON_CACHE_DIR`, `TMPDIR` 등). HOME은 그대로 둔다. 쓰기 경로가 허용 폴더 밖이면 시작을 거부한다. `HF_HUB_OFFLINE=1`로 허브에 쓰거나 내려받지 않게 한다(모델은 읽기 전용 HF 캐시에 있다).
- 왜 PYTHONPATH인가: 공유 환경을 바꾸면 남의 작업이 깨질 수 있고(되돌릴 수 없다), 같은 vllm 0.31.0이 이미 있어 우리 의존은 순수 파이썬 둘(model_runtime, boundary_contracts)뿐이다. 이 서버 환경에 `jsonschema`가 있는지는 **확인하지 못했다**(vllm 의존으로 들어 있을 것이라는 추정). 헤드가 `python -c "import jsonschema"`로 확인한다.
- 왜 0.30인가: 8B bf16 가중치 약 16GB(8.2B 파라미터 x 2바이트, 계산) + KV·활성값·CUDA 그래프 여유 약 13GB = 약 29.4GB(= 0.30 x 97.9GB). 토큰당 KV 약 144KiB(모델 카드 값을 기억에 의존, 미확인)이면 13GB는 약 9만 토큰이고 `max_num_seqs 8 x max_model_len 8192` = 약 6.6만 토큰을 담는다. **어느 수치도 측정하지 않았다.** 낮게 두는 이유: 공유 GPU에서 vLLM은 요청한 비율만큼을 시작 때 잡아 두므로(0.9면 약 88GB) 다른 사람의 작업이 메모리를 못 얻거나 우리가 시작 실패한다. 너무 낮으면(가중치 + 여유보다 작으면) 시작이 실패하고, 실패하면 값을 올려 다시 한다(`--util`). 0.30과 4GB 여유는 보수적인 추정이지 최적값이 아니다.
- 지연 숫자의 왜곡: 다른 작업이 같은 GPU의 연산 자원(SM)과 메모리 대역폭을 쓰면 지연이 부풀 수 있다. preflight는 시작 시점만 확인하고, 세션 중 15초마다 GPU 사용량과 compute 프로세스를 `logs/gpu_mem.log`에 남겨 사후에 경합 여부를 본다.

## 검토한 대안과 버리는 이유
- 서버 conda env에 pip install: 환경 수정(금지), 인터넷 필요. 기각.
- Docker 이미지로 실행: 서버에서 쓸 수 있는지 확인하지 못했고 이미지 빌드·내려받기 시간이 짧은 시간대와 맞지 않는다. 기각(미확인).
- 기본 0.9: 공유 GPU에서 타인 작업 방해. 기각.
- 모델을 ~/dabin으로 복사(16GB): 시간·디스크. 읽기 전용 HF 캐시를 오프라인으로 읽는다. 단, 허브 캐시가 읽기 전용일 때 vLLM/transformers가 잠금 파일 등을 쓰려다 실패하는지는 **미확인**(문서의 위험 항목).

## 결과
얻는 것: 한 명령으로 거부 가능한 안전한 실측, 재현 가능한 결과 JSON, 환경 무변경. 감수하는 것: GPU에서 실행해 본 적이 없다(첫 실행에서 시작 실패가 날 수 있다), 공유 GPU 경합으로 지연이 왜곡될 수 있다. 측정하지 않은 값은 '측정 안 함'으로 표시한다(프로파일 `gpu-shared-8b.json`의 `status`).

## 되돌리는 조건
첫 실제 실행에서 0.30이 시작 실패하거나 KV 캐시 부족(`max_num_seqs` 못 채움)이면 값을 올리고 이 문서와 프로파일의 근거를 실측값으로 바꾼다. 실측이 끝나면 `rtx4090-24g` 프로파일의 `status`는 헤드/영역이 별도로 갱신한다(이 키트는 24GB 장치에서 잰 것이 아니다).
