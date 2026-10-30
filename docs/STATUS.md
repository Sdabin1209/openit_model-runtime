# STATUS

갱신: 2026-10-30 (24차, 0.11.3)

- 24차(0.11.3, U400, 브랜치 wo-400): 영역 구조 개편(ADR 0028, docs/components.md). 컴포넌트 19개, schemas/ 39개(스키마 밖 조건 8건), types/ 생성, 구조 검사 통과(헤드 structure-check.py와 tests/test_structure.py). 계약 v3.1.1, openit-link v0.4.0 위로 link_receive를 다시 썼다. 시험 347 통과 2 건너뜀(이전과 같은 두 건), 이미지 빌드와 smoke.sh 통과. **남은 일**: GPU 시험은 하지 않았다(vLLM 백엔드는 모킹 시험만).

- 23차(0.7.3, 감사 MR-1/2/3/4/9/10/14/15/16 + FAULT_MATRIX A4·B1·B3 + ADR D89): CHANGELOG 0.7.3. 새 venv 187 통과 5 건너뜀, smoke 통과. **미확인**: 실제 모델 지연(요청 상한 3.5s 초기값), 토크나이저의 특수 토큰 해석, 24GB 적재, deploy가 새 기본값(토큰·ALLOW_FAKE)으로 도는지.

- 22차(0.7.2, OPEN_GAPS U16 정정 흐름): fake LORA-A가 흐름 파일 `corrections`로 CORRECT(CORRECTION 값)를 낸다(CHANGELOG 0.7.2). 시험 164 통과 1 건너뜀(xgrammar 없음), smoke.sh 통과. **미확인**: deploy E2E에서 정정 경로가 도는지(deploy 흐름 파일에 `corrections` 필요).

- 21차(0.7.1, 브랜치 `train-0.7.1`, OPEN_GAPS U16): fake LORA-A가 문항 흐름 파일의 `slots`·`skips`로 발화에서 ANSWERED 값을 낸다(규칙은 CHANGELOG 0.7.1). `serving_config` 해시 불변. `gpu-12g` 프로파일 status를 측정으로(U01). **미확인**: deploy E2E에서 이 경로가 도는지(deploy 파일에 `slots`가 있어야 함, E2E는 이 세션에서 돌리지 않음), `Q4.answer` 등 실제 칸 키가 survey-service 스키마와 같은지(시험은 turn-engine DATASET_V2 순서·건너뛰기와 시험용 칸 정의로만 확인).

- 20차(0.7.0, 브랜치 `train-0.7`, 릴리스 열차, D84): 비밀 파일화(`MODEL_RUNTIME_TOKENS_FILE`, `MODEL_RUNTIME_RELEASE_TOKEN_FILE`, boundary-contracts v2.0.2의 `read_secret`), `prompt-3`과 `tests/golden/` 제거(깨지는 변경), turn-engine 하드 핀 제거(없으면 시험 건너뜀), runbook 'LoRA 교체'. 시험·스모크 결과는 아래 커밋 보고에 따른다. push·태그 없음. **미확인**: 실제 어댑터를 GPU에서 얹는 절차(2~3단계는 model-lab·release-gate 명령, GPU 실측 없음).

- 19차(0.6.5, 브랜치 `anyof-constraint`, D83): 완화 `relax-3`. `allOf`의 `if/then(/else)`를 같은 뜻의 `anyOf` 변형으로 펼쳐 제약에 넣는다(최상위 action 변형 8, values 항목 status 변형 3). `maxItems`를 지원 집합에 추가. 상한 64, 못 펼치는 규칙은 제거+`CallInfo.relaxed`에 기록. relax-2 `serving_config`는 거부하므로 매니페스트·해시를 다시 만들어야 한다. **확인(CPU)**: 원본/변환 스키마 판정 등가(전수 조합·변이), xgrammar 0.2.7 컴파일 경고 없음, 수락 75/거부 1895 불일치 0(`scripts/xgrammar_check.py`, torch·transformers가 필요해 별도 venv). **미확인**: 실제 vLLM/Qwen3 동작·컴파일 시간, 모델 통과율. **키 순서 해결**: xgrammar가 `properties` 순서를 강제하는 문제는 계약 v2.0.1(사전순)로 고쳐졌고 이 리포는 v2.0.1로 올렸다. 사전순 5/5 수락, 거꾸로 5/5 거부(`tests/test_key_order.py`, `scripts/xgrammar_check.py`). relax는 순서를 건드리지 않는다(정본은 계약). push·태그 없음.

- 18차(0.6.4, 브랜치 `relax2-pattern`, D82): 완화 `relax-2`(`pattern`·`minItems`를 엔진에 보냄, `allOf`는 계속 제거), OUTPUT_INVALID의 `details.violations`(경로:키워드)와 로그. relax-1 `serving_config`는 거부하므로 매니페스트·해시를 다시 만들어야 한다. **미확인**: 실제 vLLM/Qwen3에서 extract 통과(GPU 없음, gpu-dev는 헤드가 확인), `minItems` 강제 여부. push·태그 없음.

- 17차(0.6.3, 브랜치 `gpu12g-qwen3-1.7b`, D81): `gpu-12g` 기반 모델을 Qwen3-1.7B(파이프라인 데모용)로 변경, 문서·시험 갱신. **미확인**: Qwen3-1.7B의 12GB 적재와 A·B 동시 적재(이 PC에 GPU 없음), 1.7B 모델 차원(HF config.json에서 받아야 함, 코드에 넣지 않음). push·태그 없음.

- 16차(0.6.2, 브랜치 `fix-fake-and-package-prompt`, deploy CR-D22 v2 1항·4항): fake의 입력 맞춤 LORA-A 출력(`MODEL_RUNTIME_FAKE_FLOW_FILE`, `MODEL_RUNTIME_FAKE_EXTRACTION`)과 `make_neutral_package.py --system-prompt-a/-b`. 자세한 내용은 CHANGELOG 0.6.2. push·태그 없음.

- 15차(0.6.1, 브랜치 `prompt-4-dataset-serialization`, D77): `prompt-4`(학습 글자와 같은 직렬화)를 추가하고 기본으로 올렸다. `prompt-3`은 폐기 표시만 하고 남겼다. 시험 `.venv/bin/python -m pytest -q` 123 통과 / 0 실패. push·태그 없음.
  - **확인한 것**: 학습 서버 val(읽기 전용, 표준출력 파이프, 저장 안 함)의 A 400행·B 400행에서 user 글자와 전체 프롬프트가 모두 일치, 키 순서를 섞은 입력(각 60행)도 일치. Qwen3-4B `tokenizer_config.json`의 chat_template을 jinja2로 렌더한 결과와 서빙 조립이 같다.
  - **미확인**: transformers `apply_chat_template` 자체와 토큰 ID 대조. 학습 때 쓴 채팅 템플릿 설정(서버 코드를 읽지 않음)과 assistant 쪽 `<think>` 처리. GPU 동작 전부. survey-service가 `state` 삽입 순서를 학습 규칙대로 만드는지(호출자 몫).
  - **미정**: `prompt-3` 제거 시점, 0.6.1 태그(헤드).

- 14차(0.6.0, 브랜치 `migrate-contracts-2.0.0`, 지시: head/docs/migration-2.0.0.md, D74·D75): 계약 v2.0.0(태그로 설치)에 맞춰 I9를 `extract`/`respond`로 바꾸고 서버 경로·헤더·OUTPUT_INVALID, 패키지 시스템 프롬프트(해시 확인), `prompt-3`(Qwen3 ChatML), 보호 정책 `protect-2`, 프로파일(Qwen3-4B/8B), 이미지 라벨을 갱신했다. 시험: `.venv/bin/python -m pytest -q` 109 통과 / 0 실패(이전 기준선 116 통과 / 2 실패는 옛 로컬 계약 0.1.0 설치 때의 값; 시험 구성이 바뀌어 개수는 비교 대상이 아니다). push·태그는 하지 않았다(헤드가 시험과 작성자를 확인한 뒤).
  - **확인한 것**: 단위·서버 시험(가짜 백엔드와 vLLM 모킹), 계약의 `HttpModelRuntime` 클라이언트로 이 서버에 돌린 적합성 시험(참조 I4 서버 + 실제 `HttpReleaseReader` 포함), CPU 이미지 빌드와 스모크(`scripts/smoke.sh`: `/healthz`, `/readyz`, 라벨, non-root).
  - **확인하지 못한 것(미확인)**: GPU에서의 모든 동작(이 PC에 GPU 없음). Qwen3-4B가 12GB에 적재되는지, A·B 어댑터 동시 적재, 24GB에서 Qwen3-8B. `ActionExtraction`을 vLLM xgrammar가 어디까지 지키는지(`pattern`, `minItems`, 배열 `type`)와 그에 따른 재시도 빈도. `prompt-3`이 Qwen3 토크나이저·학습 데이터의 형식과 같은지(CR-004 항목 2). 프로파일의 engine 값은 계산한 추정.
  - **미정/요청**: CR-004(로드 인자 `system_prompts`, 채팅 형식 대조 자료, respond 대체 문장 문구, G1 대응 검사 여부).
- 13차(0.5.3): GPU 이미지에 gcc(Triton JIT), 캐시·홈·원격 측정 ENV, 시작 시 디렉터리 보장, HF 캐시 검사 포함. 시험 118개. CPU 변형 smoke 통과. GPU 변형 빌드는 하지 않음(정적 검토만). 태그는 헤드가 붙인다.

- 12차(0.5.2): 중립 LoRA 패키지 생성기, 12GB 표준 프로파일, readyz의 I4 도달성, 런북(GPU 표준 컨테이너, 단독 compose). 시험 114개. gpu-dev에서 중립 어댑터의 실제 vLLM 로딩·출력 동일 확인(venv, 이미지 빌드·실행은 하지 않음). GPU 이미지 빌드와 12g 프로파일(0.8/8192/8)의 실측은 사용자 승인 대기.

- 11차(deploy CR-D17): 0.5.1. 번들 디렉터리 권한(이미지 소유권 + 시작 시 쓰기 확인 + readyz 사유), fake 위험 신호 스위치, `/v1/info` 기대값, 스키마 포함 빌드 옵션. 시험 105개, smoke 통과(이미지 내 스키마, 쓸 수 없는 번들 디렉터리 503). 태그는 헤드가 붙인다.

- 10차: 버전 0.5.0(pyproject, `RUNTIME_VERSION`, 이미지 라벨, CHANGELOG, 문서). 런북에 이미지 두 변형·환경 변수 표·compose 예시·fake PUT 조건 정리. 태그는 헤드가 v0.5.0을 붙인다.

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
- 이미지: 이 리포에 `Dockerfile`(CPU/GPU 변형)과 `scripts/smoke.sh`가 있다(14차 이후 CPU 빌드·스모크 확인은 위 14차 항목). 0.7.0 이미지의 빌드·스모크 재확인과 GPU 변형 빌드는 이번 점검에서 하지 않았다(미확인). `.[vllm]`과 `VLLM_USE_FLASHINFER_SAMPLER` 설정 여부는 호출하는 쪽이 정한다는 점은 그대로다.
- 사용자(도메인 소유자) 확정 대기: 대체 문장의 마지막 상수 문구(`protect-1` 잠정).
- 이 리포지토리 Git 태그 설치 경로 확인(태그 없음). push는 하지 않았다.

## 막힌 것
- 없음. (GPU 호스트 사용 규칙 D47을 지켰다: flock, 작업 폴더 `~/openit-gpu/model-runtime/`, sudo·비밀 값 없음. 호스트 쓰기는 작업 폴더와 모델 캐시뿐.)

## 알려야 할 것
- `serving_config` 형식 변경 없음. vLLM을 쓰려면 `constraint`에 `backend: "vllm"`, 정확한 `backend_version`, `DEFAULT_SUPPORTED_FEATURES`가 필요하다.

## 지시서 2 (wo2 브랜치, 2026-10-28)
한 일: MR-02(특수 토큰 prompts.py로), MR-01·MR-03 최소안(roles.py 두 컴포넌트), 모방 백엔드(`mimic`), CR-006 경계 스키마 초안 5개. ADR 0017·0018. 시험: 기존 `.venv`(turn-engine v0.4.1 설치)에서 `pytest -q -rs` 261 통과, 1 실패, 2 건너뜀. 실패 1건 `test_reference_checkout_is_the_installed_tag`는 형제 `../boundary-contracts` 작업 트리가 v2.1.1-3으로 앞서 있어서이며 이번 변경 이전 커밋에서도 같다.
남은 일: MR-03의 server.py 분리·`info()` 정리, MR-04(`GenerationRequest.input`), MR-11·MR-12(지시에 따라 손대지 않음). 이미지 빌드·스모크는 돌리지 않았다. 새 clone 두 번째 환경 확인은 하지 않았다.
