# STATUS

갱신: 2026-10-15 (9차)

- 9차(CR-003 회신): 서버를 HTTP.md I9 절(릴리스 단위 PUT/GET/parse/generate, `X-Openit-*`, 큐 503, 적재 실패 격리)로 변경, `retry_temperature` 추가, SIGTERM에서 엔진 정리(고아 EngineCore 수정). 시험 100개 통과(참조 I4 서버와 실제 HttpReleaseReader 한 바퀴 포함). gpu-dev에서 PUT 경로 한 바퀴 확인(design §11-U). 재시도 온도 0.7에서 파서·생성기 보호 개입 0/100(이전 20%·6%, 1회씩 참고값).

- 8차(D62): 추론 서버·Dockerfile(CPU 스모크 통과)·잠금 파일·4090 프로파일(잠정) 추가. gpu-dev에서 실제 서버+model-lab 어댑터로 표본 100개 시험(design §11-T): 파서 보호 개입 20%, 생성기 6%, 메모리 최대 7585 MiB, p50 0.11/0.22s. 시험 88개 통과. GPU 변형 이미지는 이 PC에서 빌드됨(13.1GB, 컨테이너 안에서 vllm 0.31.0 import 확인, GPU 없는 PC라 추론은 안 돌림). 4090 실측·survey-service 규약 일치는 확인하지 못함(변경 요청 003).

- 7차(헤드 답변 D48): 어댑터 사전 검사(r > max_lora_rank, 기반 모델 불일치, PEFT 형식 아님)를 엔진 생성 전에 추가, 0.4.0으로 올림, 재현성 약속 범위 반영. 변경 요청 002 처리됨. 태그는 헤드가 붙인다.

## 한 일
- 6차: 첫 실제 백엔드 `VLLMBackend`(vLLM + 구조화 출력 + LoRA)를 `ConstraintBackend` Protocol 뒤에 구현했다. vLLM은 선택 의존(`.[vllm]`)이고 지연 import라 없으면 `BackendNotAvailable`이다. FakeBackend와 보호 경로는 그대로다. 12GB 값은 `profiles/gpu-dev-12g.json`.
- 단위 시험 81개 통과(`.venv/bin/python -m pytest`): 기존 67 + vLLM 모킹 14(요청 조립: 스키마 완화, 시드 = 기본 + 시도, 온도, 역할별 LoRA 선택, 역할별 설정, 기반 모델 추출, 구버전 API 이름).
- GPU 시험을 gpu-dev에서 실제로 돌렸다(헤드가 pip를 설치한 뒤). 결과는 design.md §11-R. 요약: LoRA 두 개 한 기반 위에서 요청별 선택 ○, LoRA+구조화 출력 동시 ○, `allOf`·`if/then`·`not`은 조용히 무시, `uniqueItems`는 거부, 나머지 시험한 키워드는 지켜짐, 시드 재현 ○(같은 서버), 서버 재시작 간 완전 일치는 보장 안 됨, VRAM 증가분(어댑터 둘) 약 0.07 GiB. vLLM을 첫 백엔드로 확정.
- 스크립트: `scripts/gpu_check.sh`(preflight/sync/setup/run/fetch/all, flock, 분리 실행, SSH 재시도 한 번), `gpu_probe.py`, `gpu_report.py`, `make_tiny_lora.py`. 런북에 절차.
- 변경 요청 002(스키마 규칙이 제약으로 안 지켜짐, 어댑터 순위 초과 시 엔진 사망, 버전 고정, 재현성).
- design.md에 "큰 스테이션에서 재확인" 목록.

## 남은 일
- 학습된 어댑터가 나오면 시나리오 적합성 표(§11-10)와 보호 개입 빈도를 실제 백엔드로 재실행.
- 공백 무한 생성 억제 설정(xgrammar 공백 옵션) 검토. 무작위 어댑터에서 파서 출력이 닫히지 않는 경우가 많았다.
- 이미지 빌드(Dockerfile)는 이 영역이 서비스를 갖지 않아 해당 없음. 호출하는 서비스 이미지가 `.[vllm]`과 `VLLM_USE_FLASHINFER_SAMPLER` 설정 여부를 정해야 한다.
- 사용자(도메인 소유자) 확정 대기: 대체 문장의 마지막 상수 문구(`protect-1` 잠정).
- 이 리포지토리 Git 태그 설치 경로 확인(태그 없음). push는 하지 않았다.

## 막힌 것
- 없음. (GPU 호스트 사용 규칙 D47을 지켰다: flock, 작업 폴더 `~/openit-gpu/model-runtime/`, sudo·비밀 값 없음. 호스트 쓰기는 작업 폴더와 모델 캐시뿐.)

## 알려야 할 것
- `serving_config` 형식 변경 없음. vLLM을 쓰려면 `constraint`에 `backend: "vllm"`, 정확한 `backend_version`, `DEFAULT_SUPPORTED_FEATURES`가 필요하다.
