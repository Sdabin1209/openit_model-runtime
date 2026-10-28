# GPU 세션 키트 (공유 GPU에서 Qwen3-8B + LoRA A·B 실측)

목적: 허락된 짧은 시간대에 실제 GPU에서 학습된 LoRA A·B를 서빙해 extract·respond 지연(p50/p95/max)과 출력 형식 통과율을 잰다(OPEN_GAPS H1~H4, U02·U07 일부). 설계 이유는 `docs/adr/0013-gpu-session-kit.md`.

**상태: 가짜 백엔드 시험(`tests/test_gpu_session.py`)과, gpu-dev(RTX 4070 SUPER 12GB)에서 Qwen2.5-1.5B + 무작위 LoRA 2개 + 합성 4행으로 한 바퀴(`run_session.sh` -> 측정 -> 정리, 종료 코드 0, GPU 메모리가 시작 값 748MiB로 복귀)를 돌린 것이 전부다. 학습된 어댑터, Qwen3-8B, 24GB/RunPod GPU로 돌려 본 적은 없다.** 아래의 '확인하지 못한 것'을 먼저 본다.

## 키트 구성 (`scripts/gpu_session/`)
| 파일 | 하는 일 |
| --- | --- |
| `make_kit.sh` | [PC] 서버로 올릴 `kit.tgz`(model_runtime, boundary_contracts, 문진 스키마, 프로파일, 스크립트)를 만든다 |
| `preflight.sh` | nvidia-smi로 GPU 상태를 읽기만 한다. 남이 쓰는 중이면 거부(종료 코드 2). 시간 상한 `--until HH:MM`(서버 시각) |
| `prepare_bundle.py` | 어댑터 2개 + C1 시스템 프롬프트로 ReleaseManifest 형식의 릴리스 디렉터리를 만든다(어댑터 해시 포함, 계약 검증) |
| `release_server.py` | 그 디렉터리를 I4(HTTP.md) 모양으로 내주는 루프백 전용 읽기 서버. 추론 서버가 PUT 때 여기서 번들을 받는다 |
| `measure.py` | 서버에 C1 val 행을 보내 지연·오류·재시도·정답 비교를 표와 JSON으로 낸다 |
| `run_session.sh` | 위를 순서대로 실행하고 서버를 종료·정리한다 |
| `../../profiles/gpu-shared-8b.json` | 공유 GPU용 8B 프로파일(`gpu_memory_utilization` 0.30) |

## 사람이 하는 일 (헤드가 서버에서)
**서버에는 키트 코드만 올리고, 서버 쓰기는 `~/dabin` 안뿐이다.** 서버 접속과 실행은 사람이 한다(이 키트를 만든 세션은 접속하지 않았다).

1. **[PC] 키트 만들기**: 커밋된 상태에서 `scripts/gpu_session/make_kit.sh /tmp/mr-kit.tgz --python <boundary-contracts 2.0.2가 설치된 파이썬>`. 출력의 `KIT_MANIFEST.txt`(커밋, boundary 버전)를 기록한다. 서버로 `scp`한다.
2. **[서버] 풀기**: `mkdir -p ~/dabin && tar -xzf mr-kit.tgz -C ~/dabin` (결과 `~/dabin/mr-kit`).
3. **[서버] 환경 확인(읽기만)**: `PY=/home/openit/miniconda3/envs/openit-llm/bin/python`
   - `$PY -c "import jsonschema, vllm; print(vllm.__version__)"` -> `0.31.0`이어야 한다(키트의 requirements-gpu.lock과 같아야 `backend_version`이 맞는다). `jsonschema`가 없으면 멈추고 헤드가 결정한다(환경을 고치지 않는다).
   - 어댑터 두 개와 C1 폴더가 읽히는지: A = `~/taehyeon/runs/a_8b_v2/adapter`, B = `~/myungchan/runs/b_8b_1014/adapter`, C1 = `lora_a_val.jsonl`/`lora_b_val.jsonl`이 있는 폴더(서버의 `data/final`; 경로는 헤드가 확인).
   - `ls ~/.cache/huggingface/hub/models--Qwen--Qwen3-8B/snapshots` (기반 모델이 있는지).
4. **[서버] 사전 점검**: `bash ~/dabin/mr-kit/scripts/gpu_session/preflight.sh --until <허락된 시간대 끝 HH:MM>`. 거부(2)면 거기서 끝낸다. 누가 쓰는지가 출력된다. 소유자에게 알리고 기다린다. 남의 프로세스를 죽이지 않는다.
5. **[서버] 실행**:
   ```
   bash ~/dabin/mr-kit/scripts/gpu_session/run_session.sh \
     --adapter-a ~/taehyeon/runs/a_8b_v2/adapter --adapter-b ~/myungchan/runs/b_8b_1014/adapter --c1-dir <C1 폴더> \
     --until <HH:MM> --budget-min 40
   ```
   기본: A·B 각 200행, 동시 1과 4, 요청 상한 해제(순수 지연), 준비 대기 상한 15분. 처음에는 `--n-a 20 --n-b 20 --concurrency 1`로 짧게 돌려 시작이 되는지부터 보는 것을 권한다(시작 실패가 날 수 있다). 조건을 바꾼 재실행은 같은 명령에 `--util 0.40` 등.
6. **[서버] 두 번째 실행(선택)**: `--production-limits`(요청 상한 3.5초/대기 3.0초 기본값 그대로)로 `ENGINE_TIMEOUT`이 얼마나 나는지 본다.
7. **[서버 -> PC] 결과 가져오기**: `~/dabin/mr-session/run-*/`의 `measure.txt`, `measure.json`, `profile.json`, `logs/gpu_mem.log`, `logs/server.log`, `logs/prepare_bundle.log`, `logs/PREPARE_REPORT.json`(어댑터 파일 해시, 시스템 프롬프트 종류 수). 결과 파일에는 발화·모델 출력 원문이 없다(scenario_id, 길이, 판정만).
8. **끝나면**: GPU 메모리가 시작 전 값으로 돌아왔는지(`gpu_mem.log` 맨 앞 '시작 전'과 끝 '종료 후')와 `ps -u $USER`에 우리 프로세스가 남지 않았는지 확인한다. 캐시(`~/dabin/mr-session/cache`)는 재실행 속도를 위해 남기며 `--clean-cache`로 지운다.

## preflight 규칙과 이유
- 거부 조건(종료 코드 2): (1) GPU에 compute 프로세스가 있다, (2) GPU별 사용 중 메모리가 임계(기본 2048MiB, `--max-used-mib`)를 넘는다, (3) 남은 메모리가 `gpu_memory_utilization x 전체 + 여유 4096MiB`(run_session이 계산해 넘김)보다 적다, (4) `--until` 시각이 지났다.
- 이유: 공유 GPU다. 시작 때 vLLM은 요청 비율을 한꺼번에 잡으므로 남이 쓰는 중에 시작하면 우리가 실패하거나 남의 작업이 메모리를 못 얻는다. 남의 프로세스를 죽이는 것은 어떤 경우에도 하지 않는다.
- 3(nvidia-smi 없음·실패)은 종료 코드 3으로 명확히 실패한다.
- 한계: compute 프로세스 목록은 컨테이너·권한에 따라 다른 사용자의 프로세스를 못 보여 줄 수 있다(미확인). 그래서 메모리 임계도 함께 본다. preflight는 시작 시점의 스냅샷이다. 세션 중 다른 작업이 시작해도 막지 못한다(기록만 남긴다).
- 시험: `nvidia-smi`를 가짜 명령으로 대체한 pytest(`NVIDIA_SMI` 환경 변수).

## 서버 쓰기 규칙
`run_session.sh`는 작업 폴더(`~/dabin/mr-session`, `--work`)가 쓰기 허용 폴더(`$HOME/dabin`) 안이 아니면 시작을 거부한다. 모든 캐시를 그 안으로 보낸다: `VLLM_CACHE_ROOT`, `XDG_CACHE_HOME`, `TORCHINDUCTOR_CACHE_DIR`, `TRITON_CACHE_DIR`, `FLASHINFER_WORKSPACE_BASE`, `CUDA_CACHE_PATH`, `NUMBA_CACHE_DIR`, `MPLCONFIGDIR`, `VLLM_CONFIG_ROOT`, `TORCH_EXTENSIONS_DIR`, `TMPDIR`. `PYTHONDONTWRITEBYTECODE=1`(conda env에 .pyc를 쓰지 않는다), `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `VLLM_NO_USAGE_STATS=1`. `HOME`은 바꾸지 않는다. 어댑터는 읽기만 하고 `release/`로 복사한다(약 350MB, 끝에 지움). `rm`은 `${RUN:?}` 아래 자기 하위 폴더만 한다.

## 시간 상한
`--until`(서버 시각)과 `--budget-min`(기본 40분) 중 이른 쪽이 전체 상한이다. 준비 대기는 `--ready-timeout-min`(기본 15분)과 남은 시간 중 작은 값, 측정은 `남은 시간 - 90초`를 `--max-seconds`로 받아 넘기면 남은 호출을 건너뛰고 종료 코드 4로 부분 결과를 남긴다. 시간 상한에 걸리면 trap이 서버를 내린다. 모델 가중치 적재와 CUDA 그래프 준비에 걸리는 시간은 **측정 안 함**이라 첫 실행에서 15분이 모자랄 수 있다.

## 측정 방식과 표 읽기
- 입력: `lora_a_val.jsonl`의 user 메시지(ActionExtractionInput) -> `/extract`, `lora_b_val.jsonl`의 user 메시지(ResponseRequest) -> `/respond`. 행은 파일 전체에서 일정 간격으로 고른다(`--sample stride`, 앞쪽 시나리오 쏠림 방지).
- 콜드 첫 호출: 서버 시작 뒤 각 엔드포인트의 첫 호출 하나(혼자 먼저 보냄). respond의 콜드는 extract가 이미 돈 뒤라 B 어댑터 첫 사용(A는 이미 데워짐)이다.
- 이후 호출: p50/p95/max(nearest-rank)는 HTTP 200인 호출 기준. 실패 호출(예: 503 `OUTPUT_INVALID`, `ENGINE_TIMEOUT`)과 재시도 호출(`X-Openit-Attempts` > 1)은 별도 행. 재시도 호출의 지연은 재시도 횟수를 포함한 한 요청의 총 지연이다.
- 동시 요청 수 1, 4: 서버가 엔진 호출을 한 번에 하나만 하므로(ADR 0005) 동시 4에서는 큐 대기가 지연에 더해진다. p50/처리량을 동시 1과 비교한다.
- 정답 비교: A는 action 일치율, next_item 일치율, values 키 집합 일치율, 정확히 같은 JSON 비율(분모는 HTTP 200 응답 수). B는 출력이 비어 있지 않은 비율과 fallback 비율. **B의 내용 정확도(문장이 맞는지, 값을 지어내지 않았는지)는 평가 범위 밖이다.** A의 일치율도 val 한 분할의 지표일 뿐 모델 승인 기준이 아니다(그 판단은 model-lab/release-gate 몫).

## 결과로 시간 상수를 재정하는 법 (boundary.md '실측 절차'와 연결)
현재 값은 모두 **측정한 적 없는 초기값**이다: `REQUEST_DEADLINE_S` 3.5초, `QUEUE_WAIT_S` 3.0초, survey 턴 마감 6초(호출자 쪽 값), survey 읽기 timeout 4초, 입력 상한 2500자.
- **요청 상한(`REQUEST_DEADLINE_S`)**: 순수 지연 실행의 extract·respond 각각의 p95와 max(attempts=1)를 본다. 한 요청은 재시도 포함 총 상한이다. 후보 = `재시도 1회까지 포함한 호출의 p99 근처(표본이 200이라 p99는 불안정하고 max를 참고)`. 단, 반드시 survey 읽기 timeout(4초)보다 작아야 한다는 제약이 있다. 정상 호출(attempts=1)의 max가 3.5초를 넘으면 현재 상한은 정상 호출을 잘라낸다 -> 모델·프로파일을 바꾸거나(양자화, 더 작은 `max_tokens`) 호출자 쪽 timeout을 올려야 한다(변경 요청은 헤드 몫; boundary.md 불변식 참조). 이 키트는 값을 정하지 않고 입력 숫자만 낸다.
- **턴 마감 6초**: 한 환자 턴 = extract 1회 + respond 1회 직렬. 두 호출의 p95 합(콜드 제외)과 `재시도 호출`의 지연을 더한 값이 6초에서 네트워크·survey 처리 여유를 뺀 값 안에 드는지 본다. 합의 p95를 정확히 구하려면 같은 턴의 두 호출을 쌍으로 보내야 하는데 이 도구는 두 엔드포인트를 따로 재므로 **합은 근사(각 p95의 합은 상한 쪽 추정)**다.
- **큐 대기(`QUEUE_WAIT_S`)**: 동시 4 결과의 p95가 동시 1의 몇 배인지로 단일 락 구조의 대기 증가를 본다. 동시 환자 수 요구는 이 영역이 정하지 않았다(미정, 헤드 질문).
- **입력 상한 2500자**: 출력의 '입력 글자 수 p50/p95/max'와 '입력 길이 구간별 p50/p95'를 본다. 주의: C1 val의 A 입력은 짧다(수백 자). **2500자 근처 입력은 이 데이터로 재지 못한다**(지연의 길이 의존은 외삽 불가). 토큰 수는 재지 않는다(측정 안 함). 2500자 상한의 근거는 여전히 추정이다. 정말 재려면 긴 합성 입력 묶음이 필요하다(별도 작업).
- **재시도율**: `OUTPUT_INVALID` 비율과 attempts 분포. extract가 끝내 실패하는 비율이 0.x%만 돼도 턴 실패가 되므로 호출자의 재전송 정책(boundary.md) 근거가 된다.
- **A 일치율이 낮으면**: 지연이 아니라 프롬프트·키 순서 문제일 수 있다(ADR 0011, 학습 글자와의 일치). 이 도구는 원인을 가리지 않는다.

## 확인하지 못한 것 / 한계
- 실제 GPU·vLLM·학습 어댑터에서 시작해 본 적이 없다. 서버 환경에 `jsonschema`가 있는지, 읽기 전용 HF 캐시에서 `HF_HUB_OFFLINE=1`로 모델이 읽히는지(잠금 파일을 쓰려다 실패할 수 있다), 어댑터 `adapter_config.json`의 `base_model_name_or_path`가 `Qwen/Qwen3-8B`인지(다르면 `prepare_bundle`이 복사본의 이름만 바꾸고 보고서에 기록한다), FlashInfer JIT에 nvcc가 필요한지는 **모두 미확인**이다. 읽기 전용 캐시 문제가 나면 헤드가 `HF_HOME`을 ~/dabin으로 모델을 복사(약 16GB, 디스크 여유 확인 필요)해 지정할지 결정한다.
- 어댑터가 10-14 빌드로 학습됐다는 것과 prompt-4 입력 키 순서가 C1 val과 같다는 가정은 기존 문서에 의존한다. 이 키트는 C1 val의 user 메시지를 JSON으로 읽어 보내고(서버가 정렬), 그 직렬화가 학습 글자와 같은지는 ADR 0011의 검사 스크립트가 맡는다.
- **공유 GPU**: 다른 작업이 SM·메모리 대역폭을 쓰면 지연이 부풀 수 있다. preflight는 시작 시점만 본다. `logs/gpu_mem.log`에 15초마다 메모리와 compute 프로세스가 남으므로 외부 작업이 끼어든 구간이 있으면 그 구간의 지연은 신뢰하지 않는다. 지연 숫자는 'GPU가 비어 있을 때의 값'이고, 운영 환경(24GB 장치, 다른 GPU)의 값이 아니다. 이 GPU(Blackwell 97.9GB)는 `rtx4090-24g` 프로파일의 목표 장치가 아니므로 이 결과로 24GB 장치의 적재 가능성을 말할 수 없다.
- KV 캐시 크기, 적재 시간, 최대 메모리 사용은 이 실행이 처음 측정한다. 지금의 프로파일 수치는 계산한 추정이다.
- 동시 요청은 1과 4만 본다(옵션 `--concurrency`). 장시간 안정성과 어댑터 교체는 범위 밖이다.

## 시험
`../.venv/bin/python -m pytest tests/test_gpu_session.py`: preflight(통과, 사용 중, 외부 프로세스 표시, 임계, 시간 초과, nvidia-smi 없음), prepare_bundle(릴리스 검증, 시스템 프롬프트 종류 보고, 기반 모델 불일치, 순위 초과), measure 집계, run_session 한 바퀴(fake 백엔드), 쓰기 폴더 밖 거부. 같은 파일로 전체 `pytest`를 돌린다. (참고: 이 작업 이전부터 `tests/test_anyof_constraint.py` 3건이 이 환경에서 실패한다. 이 키트와 무관하다.)

## 안전 불변식 보강 (감사 라운드 2: MR-1~5)
- `run_session.sh`는 `--work`가 쓰기 허용 폴더(`~/dabin`) 안인지를 **만들기 전에** 검사한다(`realpath -m`, 심볼릭 링크 해석). 거부하면 아무것도 만들지 않는다.
- 캐시 폴더 `$WORK/cache`는 이 키트가 만든 것만 쓴다. 표식 파일 `.mr-session-owned`가 없는 비어 있지 않은 `cache`가 이미 있으면 시작을 거부(아무것도 만들지 않음). `--clean-cache`는 표식이 있고 같은 캐시를 쓰는 다른 실행(`.active.<pid>`)이 살아 있지 않을 때만 지운다.
- `prepare_bundle.py`의 `--release-id`는 `[A-Za-z0-9._-]+`이고 `.`/`..`이 아니어야 하며 `--out` 바로 아래여야 한다. `--overwrite`는 이 도구가 만든 번들(표식 `.mr-session-owned`)만 지운다.
- 값이 필요한 옵션이 마지막 인자면 사용법을 내고 **종료 코드 2**로 끝난다(무한 루프 수정). run_session.sh의 종료 코드 2는 preflight 거부와 같은 값이다(메시지로 구분한다).
- 준비 대기의 토큰은 `curl -H @파일`로 전달해 프로세스 인자(`ps`)에 보이지 않는다.

## 임대 GPU(RunPod 등)에서 쓰기: 한 명령 절차 (pod_setup.sh, pod_run.sh)
임대는 시간당 과금이라 파드를 켠 뒤 사람이 손으로 하는 단계를 없애고, 환경이 안 맞으면 **즉시 중단**하도록 순서를 정했다. 이 절은 gpu-dev 한 바퀴 실행(위 '상태')에서 얻은 결함 수정 위에 쓴 절차이며 **실제 RunPod 파드에서는 아직 돌려 보지 않았다.**

### 0. 임대 전(PC, 비용 0): 확인할 것과 사용자 결정
| 항목 | 값 / 출처 | 업로드 필요? |
| --- | --- | --- |
| 키트 코드(`make_kit.sh`) | 이 리포 + boundary-contracts 2.0.2 설치된 파이썬(`.venv`). 비밀 없음, 100KB | 필요(코드만) |
| 어댑터 A·B | 학습 산출물(서버 `~/taehyeon/runs/a_8b_v2/adapter`, `~/myungchan/runs/b_8b_1014/adapter`)을 PC로 먼저 가져와야 한다. 각 약 350MB | **사용자 결정**(학습 산출물의 외부 반출) |
| C1 val jsonl 2개 | `lora_a_val.jsonl`, `lora_b_val.jsonl`(합성 발화) | **사용자 결정**(데이터셋 소유자 확인 포함) |
| 기반 모델 Qwen3-8B 약 16GB | 파드가 Hugging Face에서 직접 내려받음(공개 모델, 토큰 불필요) | 업로드 불필요 |
| 파이썬 패키지 | `requirements-gpu.lock`(torch cu13, vllm 0.31.0 등). gpu-dev의 같은 잠금 venv가 8.3GB | 업로드 불필요(PyPI) |
`pod_run.sh`는 `--i-confirm-upload-data` 없이는 위 데이터를 **보내지 않고** 목록만 출력한다(종료 코드 3, 시험 있음).
파드 선택 조건: **CUDA 13을 지원하는 드라이버**(잠금의 torch가 cu13 휠. gpu-dev 드라이버 591.86/CUDA 13.1에서 동작 확인), python3.12, 디스크: `--base`가 있는 볼륨 40GB 이상, 외부 인터넷(PyPI·Hugging Face), 24GB 이상 GPU 1장(`rtx4090-24g.json`; A5000도 이 파일, 아래 참고).

### 1. 한 명령
```
scripts/gpu_session/pod_run.sh --ssh "-p <PORT> root@<HOST>" --adapter-a <A> --adapter-b <B> --c1-dir <C1> --i-confirm-upload-data --n-a 20 --n-b 20 --concurrency 1
```
하는 일과 중단 조건(각 단계의 '왜'는 임대 비용: 맞지 않는 환경에서 계속하면 돈만 나간다):
| 단계 | 진행 조건 | 중단 시 | 왜 |
| --- | --- | --- | --- |
| 키트 생성(PC) | `make_kit.sh`가 boundary-contracts 설치본을 읽음 | 종료 1 | 버전이 다른 계약으로 서버를 띄우면 측정이 무의미 |
| SSH·전송 | 접속·scp 성공 | 종료 1 | 비밀은 보내지 않는다: 전송 목록은 키트·어댑터·C1뿐 |
| `pod_setup.sh` 1 GPU | `nvidia-smi` 있고 CUDA Version >= 13 | 중단 메시지, 종료 2 | cu13 휠이 안 뜬다 |
| 〃 2 디스크 | `--base`가 있는 파일시스템의 남은 용량 >= 40GB | 중단 | 아래 '디스크 40GB의 근거'. RunPod에서 $HOME(container disk)이 아니라 볼륨을 검사하려고 `--base`로 모았다 |
| 〃 3 인터넷 | PyPI·huggingface.co 응답 | 중단 | 설치·모델 내려받기 불가 |
| 〃 4 설치 | python3.12 venv, lock 설치, `torch.cuda.is_available()`, vllm==0.31.0 | 중단 | backend_version이 번들과 맞아야 적재된다 |
| 〃 5 모델 | `snapshot_download` 성공 | 중단 | 이후 `HF_HUB_OFFLINE=1`로 읽기만 한다 |
| `run_session.sh` | preflight 통과, 번들 준비, 서버 준비(백엔드 기동 실패는 **기다리지 않고 즉시 중단**), 측정 | 종료 5/6/7 | 7 = 성공 호출 0건(전엔 0으로 끝났다) |
| 결과 회수 | `session-*.tgz` 저장(번들·토큰 제외) | 수동 회수 안내 | 파드를 끄면 결과가 사라진다 |
**파드를 끄기 전**: 종료 코드가 0 또는 4이고 `~/pod-results/session-*.tgz`가 0바이트가 아닌지 본다.
소요 예상: **측정 안 함**(전송 + 설치 수 분 + 8B 내려받기 + 적재). 위 표의 단계별 시각이 `[pod_run HH:MM:SS]`로 찍히므로 첫 실행 뒤 이 줄을 실측으로 바꾼다. `--skip-setup`으로 같은 파드에서 재실행하면 설치를 건너뛴다.
**RunPod 예(container 30GB + volume 50GB, /workspace, 1x RTX A5000 24GB, CUDA 13.0, 기본 사용자 root)**: `--base /workspace/dabin`을 `pod_run.sh`에 준다. venv, 키트, HF 모델, pip 임시 파일, 작업 폴더가 모두 이 아래라 container disk는 거의 쓰이지 않는다.
24GB 파드에서는 GPU를 단독으로 쓰므로 `--margin-mib 0`과 `rtx4090-24g.json`(util 0.9)을 쓴다(기본 여유 4096MiB를 쓰면 0.9x24GB+4GB가 전체를 넘어 preflight가 항상 거부한다).

### gpu-dev 한 바퀴에서 찾아 고친 결함 (모두 GPU 외 결함)
1. `XDG_CACHE_HOME`을 작업 폴더로 옮기면 HF 캐시가 안 보여 `LocalEntryNotFoundError`로 기동 실패. 옮기기 전에 `HF_HOME`을 고정했다(openit 서버에서도 같은 실패였을 결함).
2. 백엔드 기동이 실패해도 서버는 살아 있어 준비 대기 상한(15분)을 다 기다렸다. 실패 로그를 보면 즉시 중단.
3. 호출이 전부 503이어도 `measure.py`가 종료 코드 0. 성공 0건이면 7.
4. `make_kit.sh`가 `requirements-gpu.lock`을 키트에 넣지 않았다(파드에서 설치할 수 없음). 추가.
5. 기본 파이썬 경로가 openit 서버 고정이라 다른 호스트는 `--python`이 필수(`pod_run.sh`가 지정).
미해결: `deploy/test/import-v2`의 jsonl에는 의도적 불량 줄이 있어 C1 대용으로 못 쓴다(필터가 필요). `prepare_bundle`은 불량 줄을 파일:줄 번호 메시지로 거부한다(수정함). 이번 한 바퀴는 무작위 어댑터라 attempts 2~3과 A 일치율 0이 나왔고, 이는 어댑터 때문이지 키트 결함이라는 근거는 아니다. 콜드 첫 extract 12.3초는 1.5B/12GB 값이며 8B/24GB에는 적용할 수 없다.

### 디스크 40GB의 근거 (측정/추정 구분)
| 항목 | GB | 구분 |
| --- | --- | --- |
| Qwen3-8B 가중치 | 16.4 | 추정(8.2B 파라미터 x 2바이트). 내려받은 실제 크기는 측정 안 함 |
| venv(`requirements-gpu.lock`) | 8.3 | **측정**(gpu-dev에서 `pod_setup.sh` 실제 실행 후 `du`) |
| pip 휠(설치 중 임시) | 3.7 | **측정**(같은 실행의 pip 캐시 크기. `--no-cache-dir`라 설치 뒤 남지 않는다) |
| 어댑터 2개 | 0.35 | 기존 기록(문서의 약 350MB), 파드에서 측정 안 함 |
| 실행 캐시(vLLM/torch/triton) | 2 | 1.5B에서 **측정 52MB**, 8B는 값을 모르므로 넉넉히 잡은 추정 |
| 합계 | 30.8 | 위 합 |
| 기본 최소값 | 40 | 합계에 약 30% 여유(근거 없는 안전 계수, 첫 파드 실행 뒤 실측으로 바꾼다) |
50GB 볼륨의 파일시스템 오버헤드로 가용이 50보다 작아도 40은 통과한다(통과 여부는 파드에서 확인 전). 설치 시간 2분 12초는 gpu-dev 네트워크 값이고 RunPod에서는 다르다.

### RTX A5000 프로파일
별도 파일을 만들지 않고 `rtx4090-24g.json`을 쓴다. 이유: 이 프로파일의 engine 값(max_model_len, util, max_num_seqs, LoRA 슬롯)은 VRAM 24GB와 bf16에서 계산한 것이라 아키텍처에 의존하지 않고, A5000(Ampere, 24GB)도 bf16을 지원한다. 같은 내용의 파일을 이름만 바꿔 두면 두 곳을 같이 고쳐야 한다. 달라지는 것은 지연이다(메모리 대역폭이 4090보다 낮을 것으로 알고 있으나 **스펙 값은 확인하지 못했다**). **util 0.9에서 8B와 LoRA 2개가 적재되는지는 미확인**이다(`status.loadable: unverified`). 파드 GPU의 가용 메모리가 24564MiB보다 작으면 `--util 0.85`로 낮춘다.
sudo 없이·root 양쪽: 스크립트는 sudo, apt, /etc, chown을 쓰지 않고 `--base` 아래에만 쓴다(정적 확인: 해당 문자열 검색 결과 없음). root로 도는지는 파드에서 확인 전.

## RunPod A5000 24GB: 임대 전 사전 점검 결과와 시간순 한 장 (PC에서, GPU 없이, 실제 학습 산출물로)
**확인한 입력**(리포 밖, 이 PC 밖으로 보내지 않음): 어댑터 A·B(각 safetensors 174,655,536바이트), C1 `lora_a_val.jsonl` 3,221줄 / `lora_b_val.jsonl` 3,233줄 전체. 어댑터·C1은 이 문서에 값을 복사하지 않는다(해시는 `logs/PREPARE_REPORT.json`).

### 사전 확인 가능했던 것(지금 확인함)과 GPU로만 확인 가능한 것
| 파드에서 첫 실패가 날 만한 지점 | 구분 | 결과 / 근거 |
| --- | --- | --- |
| `prepare_bundle`: ReleaseManifest 계약 검증, 불량 줄, 어댑터 해시, 시스템 프롬프트 종류 | 사전 | 실제 A·B·C1 전체로 통과. 경고 0. A 시스템 프롬프트 1종(927자), B 1종(281자). 불량 줄 0 |
| 어댑터 `base_model_name_or_path` | 사전 | 둘 다 `Qwen/Qwen3-8B`(프로파일과 일치, 이름 재작성 불필요) |
| rank / target_modules vs `max_lora_rank 16` | 사전 | r=16, lora_alpha=16, 7개 모듈(q,k,v,o,gate,up,down), use_dora=false, modules_to_save=null, rank/alpha_pattern 비어 있음 |
| safetensors 헤더(로드 없이) | 사전 | 텐서 504개/어댑터, 전부 `lora_A/B.weight` 쌍, F32, 36층 x 7모듈. shape이 Qwen3-8B 차원(hidden 4096, q/o 4096, k/v 1024, MLP 12288)과 504개 모두 일치. 헤더가 말하는 크기 = 실제 파일 크기. 이 검사를 `prepare_bundle`에 넣었다(`check_safetensors_header`, 시험 있음) |
| 학습 토크나이저 = 서빙 모델 토크나이저 | 사전 | HF `Qwen/Qwen3-8B`의 `tokenizer.json`과 어댑터 폴더의 것은 어휘·merges·normalizer 동일(직렬화 플래그만 다름). C1 6,454행 전체를 두 토크나이저로 인코딩해 토큰 ID 불일치 0건. `chat_template`은 HF 원본과 바이트 동일 |
| 서빙 프롬프트 렌더(prompt-4) = 학습 글자 | 사전 | `render_prompt`(서버가 실제로 쓰는 함수)의 결과가 학습 `chat_template`(enable_thinking=False) 렌더와 A 3,221/3,221, B 3,233/3,233행에서 일치. 학습 전체 렌더 = 프롬프트 + 정답 + `<|im_end|>\n`도 전 행 일치. user 글자 일치 전 행. 정답 A 3,221행이 출력 스키마 통과 |
| 입력 길이 / 2500자 상한 | 사전 | 서버와 같은 정의(`len(json.dumps(body))`): A p50 271 / p95 382 / max 574자, B p50 240 / p95 309 / max 394자. 2500 초과 0건. 이 데이터로는 상한 근처를 못 잰다(이미 아는 한계) |
| 토큰 길이 vs `max_model_len 8192`, `max_tokens` | 사전 | 프롬프트 토큰 max A 765 / B 386. 정답 토큰(+EOS) max A 203 / B 86 -> `max_tokens` A 512 / B 256 이내(초과 0건), 8192 초과 0건 |
| 키트 전체(`run_session.sh`, fake 백엔드, 실제 어댑터·C1, `--n-a 20 --n-b 20`, 동시 1과 4) | 사전 | make_kit로 만든 키트를 풀어 실행. 둘 다 종료 코드 0, LOADED, 20/20 성공. 결과·로그에 C1 발화 원문 없음(400행 발화로 검색). 새 결함 없음 |
| `pod_setup` 환경 점검 | 사전(보강함) | C 컴파일러·`Python.h` 점검을 설치 전에, GPU bf16 행렬곱 점검을 설치 직후에 추가(Triton이 런처를 빌드한다). '있음' 경로만 PC에서 확인했다 |
| Qwen3-8B 내려받기 크기 | 사전 | HF API 합계 16.40GB(15.27GiB), 5 샤드, 비공개 아님/게이트 아님(토큰 불필요). 디스크 40GB 근거의 추정 16.4GB가 맞다 |
| `requirements-gpu.lock` 이 파드(py3.12, x86_64 glibc<=2.35)에서 설치 가능한가 | 일부 | 확인한 것: 핀 `llguidance==1.7.6`은 `manylinux_2_31_x86_64` 휠만 있다(glibc 2.31 이상 파드 필요, Ubuntu 22.04는 2.35라 충족). 전체 핀의 휠 존재 확인(`pip install --dry-run --report`)은 휠을 내려받느라 400초 상한에 걸려 **끝내지 못했다**. gpu-dev에서 같은 lock이 실제로 설치된 것이 근거 |
| 드라이버 CUDA >= 13 | GPU(파드)로만 | `pod_setup` 1단계가 설치 전에 멈춘다. RunPod 배포 화면에서 CUDA 13 필터를 거는 것이 사전 조치 |
| python3.12·gcc·Python.h 유무 | 파드로만 | `pod_setup`이 설치 전에 중단 |
| 24GB에 util 0.9로 8B + LoRA 2개 적재, KV 캐시 크기 | GPU로만 | 추정만: 가중치 15.3GiB, util 0.9의 예산 약 21.6GiB, 토큰당 KV 144KiB(Qwen3-8B 구성에서 계산) -> 남는 KV 몇 GiB(수만 토큰). 측정 안 함. 실패하면 `--util 0.85` |
| vLLM이 이 `adapter_config.json`(peft 0.21 필드 다수)을 읽는지, F32 가중치 적재 | GPU로만 | 지원하지 않는 형태(DoRA, modules_to_save, rank_pattern)는 사전 배제함. 나머지는 vLLM 코드를 실행해야 앎 |
| 구조화 출력(xgrammar) 경로, 서버 기동 | GPU로만(부분) | vllm 0.31.0 + 같은 lock으로 gpu-dev(1.5B)에서 이미 한 바퀴 돎. Qwen3-8B/Ampere에서는 미실행 |
| 적재·CUDA 그래프 시간, 지연, 처리량, A 일치율 | GPU로만 | 측정이 목적 |

### 한 장: 파드에서 할 일 (시간순)
비용 시계는 **파드 시작(과금 시작)부터 중지까지**다. PC에서 하는 구간(1~2)은 파드가 켜진 뒤라면 과금된다. 그래서 파드는 3번 명령 직전에 켠다.
| # | 구간 | 하는 것 | 시계 | 중단 조건 |
| --- | --- | --- | --- | --- |
| 0 | 켜기 전(PC) | 위 표 확인 완료. RunPod: 1x A5000, **CUDA 13 필터**, 볼륨 50GB(`/workspace`), 컨테이너 디스크 30GB, 퍼블릭 IP/SSH. 사용자 결정: 어댑터·C1 업로드 허락 | 꺼짐 | 허락 없으면 여기서 끝 |
| 1 | 파드 켜짐 ~ SSH 가능 | 호스트·포트 확인 | 켜짐 | 접속 안 되면 파드 교체 |
| 2 | 명령 한 줄 | `scripts/gpu_session/pod_run.sh --ssh "-p <PORT> root@<HOST>" --base /workspace/dabin --adapter-a <A> --adapter-b <B> --c1-dir <C1> --i-confirm-upload-data --n-a 20 --n-b 20 --concurrency 1` | 켜짐 | 아래 단계별 |
| 2a | 키트 생성·전송 | 키트(약 100KB) + 어댑터 약 350MB + C1 약 12MB 전송 | 켜짐 | SSH/전송 실패 -> 종료 1 |
| 2b | `pod_setup` | GPU/드라이버 CUDA>=13 -> 디스크>=40GB -> PyPI/HF 도달 -> python3.12 -> gcc, Python.h -> lock 설치 -> torch GPU, 행렬곱 -> vllm==0.31.0 -> Qwen3-8B 16.4GB 내려받기 | 켜짐(가장 긴 구간 후보: 내려받기·설치, **측정 안 함**) | 어느 단계든 첫 실패에서 종료 2(파드를 끄고 원인 수정) |
| 2c | `run_session.sh` 짧은 실행 | preflight(마진 0) -> 번들 준비 -> 서버 기동 -> 적재 -> 20+20건 측정 | 켜짐 | 5(기동·적재 실패, 즉시 중단) / 6(시간 초과) / 7(성공 0건). 7이면 `logs/server.log` 마지막 30줄을 보고 중지 |
| 2d | 결과 회수 | `~/pod-results/session-*.tgz`(번들·토큰 제외) | 켜짐 | tgz가 0바이트면 파드 끄지 말고 수동 회수 |
| 3 | 확장 실행(선택) | 짧은 실행이 성공하면 같은 파드에서 `--skip-setup`으로 `--n-a 200 --n-b 200 --concurrency 1,4`(필요시 `--production-limits` 별도 실행) | 켜짐 | 위와 같음 |
| 4 | 끄기 | 종료 코드 0 또는 4, tgz 크기>0 확인 후 파드 중지(볼륨은 다음에 쓸 거면 유지, 아니면 삭제) | **꺼짐** | |
회수물: `measure.txt`, `measure.json`, `profile.json`, `logs/{gpu_mem.log, server.log, prepare_bundle.log, PREPARE_REPORT.json}`(발화·모델 출력 원문 없음). 3의 소요 추정(측정 안 함, 계산): A5000 메모리 대역폭을 768GB/s로 알고 있으나 **확인하지 못했고**, 이 값과 16.4GB로 디코딩 상한 약 46토큰/초 -> 정답 길이(A p50 96토큰, B p50 48토큰)로 extract 약 2~3초, respond 약 1~2초 -> 200+200건 동시 1은 10~20분. 그래서 기본 예산 45분 안에서 확장 실행은 한 번이 안전하다. 현재 3.5초 요청 상한은 extract에서 넘길 가능성이 높다(이 값도 예측일 뿐).
