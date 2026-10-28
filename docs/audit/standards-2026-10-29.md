# model-runtime 표준성·근거 전수 점검 (2026-10-29)

기준서: `head/docs/audit/standards-audit-spec.md`(D89). 대상: main의 `c81d2e1`(0.7.7). 읽기 전용 점검이며 코드·설정·시험은 고치지 않았다. 시험은 돌리지 않았고 GPU에서 확인한 것은 없다. 아래 "표준" 칸은 내가 정확히 아는 이름만 썼고, 모르는 것은 "표준 여부 확인 못 함" 또는 "이름 있는 표준 없음"이라 썼다. 이름 있는 표준이 없는 항목은 ADR이 대안을 비교했는지로 N/N-를 갈랐다. 단위는 결과표의 행 하나다(묶은 행은 하나로 센다).

읽은 것: `CLAUDE.md`, `README.md`(앞부분만 확인, 아래 '점검하지 못한 것'), `docs/design.md`, `docs/brief.md`, `docs/rules.md`, `docs/adr/0001~0018` 전부, `src/model_runtime/{server,session,roles,prompts,protection,fallback,serving_config,vllm_backend}.py` 전체, `schema_guard.py`·`mimic.py`·`backends.py` 일부, `profiles/*.json` 4개, `Dockerfile`, `pyproject.toml`, `scripts/gpu_session/{pod_setup.sh,make_kit.sh,util_retry.py}` 및 `run_session.sh` 일부, 그리고 읽기가 허용된 `../boundary-contracts`(HTTP.md, CONVENTIONS.md, clients/releases.py).

## 결과표

| # | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
|---|---|---|---|---|---|
| 1 | 추론 엔진 vLLM(프로세스 안 엔진) | N- | 이름 있는 표준 없음(ADR 0001도 "표준: 없음"). | ADR 0001. 요구 세 가지(경로 LoRA, JSON 스키마 제약, 요청별 시드)를 gpu-dev에서 확인했다고 적었다. 그러나 TGI·llama.cpp·transformers는 "근거 기록 없음"이라고 ADR이 스스로 적었다. 대안 비교가 없다. | `vllm_backend.py:283-326`, `requirements-gpu.lock:194` |
| 2 | 구조화 출력 3겹(제약 디코딩 + 원본 스키마 사후 검증 + 재시도). 스키마는 JSON Schema Draft 2020-12 | N | JSON Schema 2020-12(스키마 언어). 엔진 쪽 제약 디코딩 자체는 표준 없음. | ADR 0002, D75, D82, D83. "제약 없이 검증+재시도만" 대안은 비교 측정이 없다고 명시했다. 사후 검증이 항상 있어 안전 쪽이라는 논리는 있다. | `roles.py:101-113`, `serving_config.py:136-178` |
| 3 | 제약 디코딩 문법 엔진 xgrammar | ? | 확인 못 함. 코드는 xgrammar를 지정하지 않는다. `structured_outputs_backend`/`guided_decoding_backend`는 허용 키일 뿐 어떤 프로파일도 설정하지 않아, 실제로 어느 엔진이 쓰이는지는 vLLM 0.31.0 기본값에 맡긴다. | ADR 0002는 xgrammar 0.2.7을 전제로 쓰지만 선택·고정 근거가 없다. 락 파일에 xgrammar==0.2.7만 있다. | `vllm_backend.py:94-99`, `profiles/*.json`(키 없음), `requirements-gpu.lock:197` |
| 4 | 완화 스키마 relax-3(allOf/if-then을 anyOf 변형으로 직접 펼침, 변형 상한 64) | N | 없음. JSON Schema의 `allOf`/`if`/`then`을 엔진이 못 강제해서 만든 변환기. | ADR 0002, `tests/test_anyof_constraint.py`(동등성 전수 시험), CHANGELOG 0.6.5. 직접 만든 이유(엔진이 allOf를 무시)는 gpu-dev 측정으로 있다. 상한 64의 값 근거는 기록 없음. | `schema_guard.py:97`, `schema_guard.py:240-300` |
| 5 | 사후 검증기에서만 `pattern`의 `$`를 `\Z`로 읽음 | S | JSON Schema의 `pattern`은 ECMA-262 정규식이다. 파이썬 `re`의 `$`가 끝 줄바꿈을 허용하는 차이를 보정한다. | ADR 0002 결과(c), MR-14, 주석. | `schema_guard.py:44-57` |
| 6 | LoRA 두 개를 한 엔진에 올림(`max_loras` 2, 요청별 선택) | N- | 이름 있는 표준 없음. vLLM의 다중 LoRA 기능을 쓴다. 어댑터 형식은 PEFT `adapter_config.json`(Hugging Face PEFT의 파일 규약. 표준 여부 확인 못 함). | ADR 0003의 이유는 "가중치 한 번만 쓴다"는 계산뿐이다. 역할별 프로세스·병합 모델 대안은 "비교 기록 없음". 동시 적재는 `two_adapters_loaded_together: unverified`. | `vllm_backend.py:315-318`, `profiles/gpu-12g.json`, ADR 0003 |
| 7 | `max_lora_rank` 16 | S- | 학습 어댑터 rank와 일치해야 한다는 제약이다(표준 아님, 제약). | ADR 0003, profiles 주석에 "D74". 값의 출처가 제약이라는 점은 있으나 어댑터 rank의 정본(학습 설정) 링크는 없다. | `profiles/rtx4090-24g.json`, `vllm_backend.py:342` |
| 8 | 릴리스 보존 `MAX_RELEASES` 4 | N- | 없음. | ADR 0003이 "근거 기록 없음(롤백용 여유로 추정)". | `server.py:133`, `server.py:585` |
| 9 | 릴리스 번들 적재(`HttpReleaseReader`)와 모든 해시 검증 | S | 해시·정규화는 계약의 기준 구현을 가져다 썼다. | ADR 0004, HTTP.md I4. 해시 검증은 계약 클라이언트가 한다(어댑터 디렉터리, 문항 은행 등). | `server.py:277`, `../boundary-contracts/boundary_contracts/clients/releases.py:44-68` |
| 10 | `serving_config_hash` = 설정을 JCS 직렬화해 SHA-256 | S | RFC 8785(JSON Canonicalization Scheme), FIPS 180-4(SHA-256). 구현은 `boundary_contracts.canonical`을 쓴다. | ADR 0004, CONVENTIONS | `serving_config.py:133`, `serving_config.py:201-204` |
| 11 | 시스템 프롬프트 파일 해시 확인과 경로 이탈 차단(`is_relative_to`) | S | 해시는 `canonical.file_hash`(계약 구현). 경로 이탈 검사는 일반 보안 관행이며 이름 있는 표준을 특정하지 못함. | ADR 0004, D75 | `session.py:102-115` |
| 12 | 매니페스트 `model_runtime_version`의 MAJOR.MINOR가 서버와 같아야 함 | N- | SemVer 2.0.0은 MINOR가 호환 추가라고 정의한다. 이 규칙은 그보다 엄격하며 이유가 없다. | ADR 0004가 규칙만 말하고 왜 MAJOR.MINOR인지 쓰지 않았다. | `server.py:281` |
| 13 | 직렬화·인증 보조: `/v1/info`에 `system_prompt_sha256`을 `sha256:` 접두 형식으로 직접 계산 | S- | SHA-256. 접두 형식은 계약의 해시 표기를 따른 것으로 보이나 이 파일에서 확인한 근거 문구는 없다. | 근거 기록 없음(계약 구현과 같은 값이 나오는지는 시험 `test_server` 등을 열지 않아 확인 못 함) | `session.py:66-67` |
| 14 | HTTP 서버: `http.server.ThreadingHTTPServer` | N- | 파이썬 문서는 `http.server`가 기본적인 보안 검사만 한다고 경고한다(내 지식, 이 점검에서 문서를 다시 열지는 않았다). HTTP/1.1 자체는 RFC 9110/9112. | ADR 0010이 "선택 당시 비교 기록 없음", "FastAPI 비교·측정 안 함"이라고 명시했다. 이후 붙인 이점은 사후 설명이라고 ADR이 인정한다. | `server.py:37`, `server.py:86-108` |
| 15 | 연결 상한·소켓 timeout·본문 상한·Content-Length/Transfer-Encoding 검증·깊은 JSON 방어·우아한 종료를 직접 구현 | O | 이런 것은 웹 서버/프레임워크(예: uvicorn, gunicorn류)가 제공하는 영역이다. 이 영역은 직접 만들었다. | ADR 0010이 "감수하는 것: 느린 클라이언트 방어를 직접 한다"고 기록했으나 직접 만든 것에 대한 가져다 쓰지 않은 이유(비교)는 없다. ADR 0012는 무엇을 막는지만 쓴다. | `server.py:86-108`, `server.py:446-451`, `server.py:592-611` |
| 16 | 인증: `Authorization: Bearer` 토큰, 상수 시간 비교(`hmac.compare_digest`), 기본 닫힘 | S- | RFC 6750(Bearer). 상수 시간 비교 관행. 스킴 이름 대소문자 비구분(RFC 9110)인데 코드는 `startswith("Bearer ")`라 대문자 B만 받는다(사소). | ADR 0012는 "일반적인 보안 관행"이라고만 쓰고 표준 이름을 대지 않았다. 왜 정적 공유 토큰인가(OAuth2, mTLS와의 비교)는 없다. | `server.py:433-444`, `server.py:526-529` |
| 17 | 토큰 설정 형식 `역할=토큰,역할=토큰`(쉼표·등호 구분 문자열) | N- | 이름 있는 표준 없음(RFC 아님). | ADR 0012가 시작 거부 규칙만 쓰고 형식 선택의 이유와 대안(JSON·파일 한 줄 하나)은 없다. 토큰에 쉼표가 들어가면 쓸 수 없다는 제약도 문서에 없다. | `server.py:70-83` |
| 18 | 비밀은 `NAME_FILE`로 파일에서 읽음 | S | 12-factor는 설정을 환경 변수로 두라지만 비밀은 파일(예: Docker/Kubernetes secrets 마운트 관행). 워크스페이스 D84. 구현은 계약의 `read_secret`. | D84, `server.py:15` 주석 | `server.py:523`, `server.py:572` |
| 19 | 자격 발급·보관·갱신·폐기 | ? | 이 영역은 토큰을 발급하지 않는다. 받아서 비교만 한다. 토큰 교체 절차(재시작 필요 여부 등)는 이 리포 문서에서 찾지 못했다. | 근거 기록 없음. 발급과 갱신이 deploy 몫인지 문서로 확인하지 못했다. | `server.py:523-531` |
| 20 | `loader`/`PUT_ROLES` 역할 분리, 기본은 인증된 모든 역할이 PUT 가능 | N | 이름 있는 표준 없음(최소 권한 원칙은 일반 원칙). | ADR 0012 대안 절: 기본 강제 시 survey-service의 404 재적재가 깨져서 메커니즘만 제공한다. | `server.py:62`, `server.py:474-476` |
| 21 | `/healthz`(생존)·`/readyz`(준비) 분리, 비인증 `/readyz`는 사유 숨김 | S- | Kubernetes의 liveness/readiness 프로브 관행, 워크스페이스 공통 규칙(D22), FAULT_POLICY 5. | ADR 0012, 계약 CONVENTIONS "상태 확인 응답 시간". 이름 있는 표준을 직접 인용하지는 않았다. | `server.py:455-463` |
| 22 | 요청 총 상한 3.5초(`REQUEST_DEADLINE_S`) | N- | 상한 방식(바깥이 안쪽보다 길어야 한다)은 계약 CONVENTIONS의 "시간 상한 사슬"(프로젝트 내부 규칙). 값 3.5 자체는 측정하지 않았다. | ADR 0006: 3.5는 survey 읽기 timeout 4초보다 짧아야 한다는 제약에서 나왔다. 3.5/3.0/0.5초 여유의 구체 선택은 "근거 기록 없음", 실제 모델 지연은 측정하지 않았다. 사슬 표가 "잠정값"이라 인정한다. | `server.py:136`, `server.py:583`, CONVENTIONS "시간 상한 사슬" |
| 23 | 대기 상한 `QUEUE_WAIT_S` 3.0, `PUT_WAIT_S` 3.0 | N- | 없음. HTTP.md I9는 "호출 타임아웃(4초)을 넘을 것 같으면 503"이라고만 한다. | ADR 0006이 3.0을 "HTTP.md 4초보다 작게 둔 값"이라고만 쓴다. PUT_WAIT_S는 ADR에 근거 없음. | `server.py:133`, `server.py:581-582` |
| 24 | 대기 예측식 `(대기+1)×이동평균`, 초기 평균 0.5초, 가중 0.8/0.2 | N- | 지수 이동 평균(일반 기법). 계수 선택은 표준 없음. | 근거 기록 없음(ADR 0005는 식을 쓰지만 0.5, 0.8/0.2의 이유는 없음). | `server.py:149-151`, `server.py:350`, `server.py:367` |
| 25 | 엔진 호출을 락 하나로 직렬화(동시성 모델) | N | 이름 있는 표준 없음. HTTP.md I9가 병렬·짧은 대기열 둘 다 허용한다. | ADR 0005. 직접 이유는 `last_call_info` 공유 상태 경쟁이다. vLLM 스레드 안전성은 "확인하지 못함". 병렬은 시도·측정하지 않았다. | `server.py:149`, `server.py:357-379` |
| 26 | 상한 구현: 호출마다 스레드를 띄워 기다리다 포기, 엔진 호출은 중단하지 않음 | N | 파이썬 스레드는 강제 중단할 수 없다는 제약. 엔진의 요청 abort는 쓰지 않는다(vLLM에 그런 기능이 있는지 확인 못 함). | ADR 0006 대안 절. | `server.py:362-379` |
| 27 | 출력 재시도 최대 3회, 시드 = 기본 시드 + 시도 번호 | N- | 이름 있는 표준 없음. 시드 규칙은 INTERFACES I9(프로젝트 계약)다. | ADR 0007: 값 3의 근거는 "헤드 승인"뿐이고 2나 5보다 낫다는 측정은 "근거 기록 없음". 승인은 이유가 아니다. 시드 규칙·프롬프트 불변은 I9에 근거가 있다. 상한 5도 "제안"이다. | `serving_config.py:159`, `serving_config.py:181`, `roles.py:45-50` |
| 28 | 재시도 3회와 요청 총 상한 3.5초의 합성 | ? | 확인 못 함. 8B 한 호출이 3.5초를 넘으면 재시도는 실행되지 못하고, 한 환자 턴(extract+respond)의 최악은 3.5×2=7.0초라 사슬의 survey 턴 마감 6초를 넘을 수 있다. 이 계산을 ADR이 하고 있는지 못 찾았다(ADR 0006은 "6초"만 언급). | 근거 기록 없음. H1 실측 전에는 판단 불가. | `server.py:356-379`, ADR 0006 결과 절, CONVENTIONS "시간 상한 사슬" |
| 29 | 입력 길이 한도 2500자(JSON 직렬화 문자 수) | N | 없음. 4096 토큰 − 출력 512 − 시스템 프롬프트 약 1000(가정)을 1자=1토큰으로 환산한 추정. | ADR 0012: 값은 추정(토큰 수 측정 안 함, 시스템 프롬프트 1000토큰은 가정)이라 밝히고, 학습 데이터 64,459행 전수 대조(최대 643자)로 3.9배 여유를 확인했다. 한도를 프로파일(8192)과 연결하지 않는 점은 기록 없음. | `server.py:57`, `server.py:354` |
| 30 | 기타 한도: 소켓 timeout 15s, 동시 연결 32, 동시 적재 2, 실패 보존 16, 종료 대기 5s, 본문 4MiB, 릴리스 읽기 timeout 120s, 백엔드 재시도 간격 30s, 도달성 캐시 5s·probe timeout 2s, release_id 길이 128 | N- | `release_id` 형식은 계약의 `Id`(128자)와 같다(CONVENTIONS 확인). 나머지 값은 없음. | ADR 0012가 "측정·표준에서 나온 값이 아니다 … 근거 기록 없음(초기값)", 4MiB·120s·30s도 코드/ADR에 "근거 기록 없음"이라 적었다. 5초 캐시와 2초 probe는 CONVENTIONS의 `/readyz` 2초 규칙과 맞다. | `server.py:56-65`, `server.py:217`, `server.py:572`, `server.py:608` |
| 31 | 프롬프트 직렬화 `json.dumps(ensure_ascii=False)`, A는 최상위만 정렬, B는 전 수준 정렬(`prompt-4`) | N | JCS(RFC 8785)를 일부러 쓰지 않았다. 학습 데이터 글자와 같아야 한다는 제약. | ADR 0011, D74, D77. 학습 val 대조 스크립트와 C1 전수 대조(`check_reference_data.py`). A의 하위 키 순서를 호출자 책임으로 넘긴 점도 이유가 적혀 있다. | `prompts.py:56-63`, `prompts.py:67` |
| 32 | 채팅 형식 문자열을 직접 조립(Qwen3 ChatML, `enable_thinking=False`) | O | 모델 제공자의 토크나이저가 `apply_chat_template`로 제공하는 것이다. 이 영역은 f-string으로 직접 만들고 jinja로 렌더한 결과와 대조했다(`transformers`의 `apply_chat_template` 자체는 실행하지 않았다고 prompts.py가 인정). 대조 기준은 Qwen3-4B이며 현재 프로파일의 1.7B·8B 토크나이저와는 대조하지 않았다. | 직접 만든 이유(왜 `apply_chat_template`를 안 쓰는가)는 기록 없음. 가설(순수 함수, CPU 이미지에 transformers 불필요)은 내 추측이다. | `prompts.py:50-53`, `prompts.py:19-21`, `scripts/check_chat_template.py` |
| 33 | 입력 문자열 속 채팅 특수 토큰 모양 무력화(`<`+영문자/`_`/`/`/`\|` 뒤에 공백, `\|>`→`\| >`) | N | 이름 있는 표준 없음. 프롬프트 주입에 대한 OWASP LLM 가이드가 있으나 이 방식을 요구하는지는 확인 못 함. 토크나이저의 특수 토큰 해석 옵션(가져다 쓸 수 있는 기능)과 비교했는지는 기록 없음. | ADR 0011, MR-2. 대안 비교는 JCS 직렬화 등 다른 것이고, 토크나이저 옵션과의 비교는 없다. 학습 데이터 64,459행에서 바뀌는 행 0건 확인. 실제 토크나이저가 텍스트 속 특수 토큰을 해석하는지는 GPU에서 미확인. | `prompts.py:33-47`, `prompts.py:91` |
| 34 | 출력 보호 정책 `protect-2`(A는 보호 없이 OUTPUT_INVALID 503, B는 형식 위반 시 재료를 이은 대체 문장) | N | 이름 있는 표준 없음(도메인 정책). | D36, D75, I9 계약, `docs/rules.md`. 대체 문장 문구는 "잠정, 도메인 소유자 확정 대기"라고 주석에 적혀 있다. 대안(A도 대체값) 기각 이유: 값을 지어내지 않는다(D75). | `protection.py:114-125`, `fallback.py:16-25`, `roles.py:106-145` |
| 35 | fake 백엔드를 운영 이미지에 포함하고 `ALLOW_FAKE=1` 없으면 시작 거부 | N | 이름 있는 표준 없음. 워크스페이스 D22(이웃의 메모리 구현으로 단독 시험)가 요구하는 것은 가짜 구현의 존재다. | ADR 0009. 별도 이미지 대안은 "비교 기록 없음". 환경 변수 하나로 켜지는 가드가 충분한지는 기록 없음(운영 이미지에 ALLOW_FAKE가 들어오면 막을 수 없다). | `server.py:536-538`, `server.py:549-551` |
| 36 | mimic(모방) 백엔드, 규칙표를 JSON 데이터 파일로 분리, 입력 JSON의 sha256으로 문장 선택 | N | 이름 있는 표준 없음. | ADR 0018(대안 2개 기각 이유 있음). 한국어 부분 문자열 일치의 오판은 알려진 한계로 기록. 문구는 임상 검토 전. 가드는 35번과 같다. | `server.py:549-557`, `mimic.py:239`, `data/mimic_rules.json` |
| 37 | 프로파일 JSON 파일(엔진 인자 + 주석 + status) | N | 이름 있는 표준 없음. 설정은 환경 변수/파일(12-factor 3항)이라는 관행은 있다. | ADR 0008, 공통 규칙 D47. 형식은 `load_profile`이 engine 키 화이트리스트만 검사한다. 형식 스키마는 변경 요청 CR-006(초안)에 있으나 정본이 아님. `pod_setup.sh`는 JSON 파서 없이 `sed`로 `base_model` 한 줄을 읽는다(ADR 0015가 한계를 적음). | `vllm_backend.py:257-268`, `pod_setup.sh:34` |
| 38 | `gpu-12g`: Qwen3-1.7B, `gpu_memory_utilization` 0.8, `max_model_len` 4096, `max_num_seqs` 4 | N | 없음. | ADR 0008. 값은 이전 프로파일(0.8/8192/8)에서 출발한 보수 추정이고 gpu-dev 1회 적재(약 10.1GB)로 확인했다. `max_model_len`·`max_num_seqs` 선택 이유와 동시 적재는 측정하지 않았다. 문서 불일치: `docs/design.md`는 `gpu-12g`를 Qwen3-4B라 쓰지만 프로파일·ADR 0008은 1.7B다. | `profiles/gpu-12g.json`, `docs/design.md`(프로파일 행) |
| 39 | `rtx4090-24g`: Qwen3-8B, util 0.9, `max_model_len` 8192, `max_num_seqs` 8 | N- | 없음. | 프로파일 주석이 "측정이 아니라 계산한 추정, 모델 카드 수치를 기억에 의존, 미확인"이라 적었다. 0.9를 고른 이유와 8192/8 선택 이유는 없다. 이 하드웨어에서 적재한 적 없음. | `profiles/rtx4090-24g.json`, ADR 0008 |
| 40 | `gpu-shared-8b`: util 0.30 | N | 없음. | ADR 0013: 공유 GPU에서 필요한 만큼만 잡으려는 계산(가중치 약 16GB + 여유 13GB). 어느 수치도 측정하지 않았다고 명시. 값을 틀리면 실패하고 올려 다시 한다는 되돌리는 조건이 있다. | `profiles/gpu-shared-8b.json`, ADR 0013 결정 4 |
| 41 | util 자동 재시도 0.90→0.85→0.80(0.05씩, 최대 2번) | N- | 이름 있는 표준 없음. | ADR 0016: "측정이 아니라 지시된 값". 지시는 이유가 아니다. 0.05 단위는 12GB급 기준 추정(약 0.6GiB)이며 24GB(1.2GiB)·97.9GB(4.9GiB)에서는 크기가 다른데 같은 0.05다. 메모리 부족과 KV 부족을 구분하고 후자는 재시도하지 않는다는 판단은 이유가 있다. | `util_retry.py:20`, `run_session.sh:202`, ADR 0016 |
| 42 | 메모리 부족 판별을 vLLM 로그 문구 정규식으로 함 | N | 종료 코드·API가 없다는 전제. 정규식 문구 중 하나만 실기에서 재현했다. | ADR 0016: 틀리면 `other`로 분류돼 즉시 멈추는 안전 쪽이라는 논리가 있다. 나머지 문구는 미확인. | `util_retry.py:17-19` |
| 43 | 의존 고정: `requirements-*.lock`(`==` 핀, 해시 없음) + 태그 설치 | S- | pip의 requirements 핀 고정 관행. 해시 검증(`--require-hashes`)은 쓰지 않는다(락 파일에 `--hash` 0건). | 워크스페이스 규칙(공통 CLAUDE.md), Dockerfile 주석. 해시 고정을 하지 않은 이유는 기록 없음. | `requirements-gpu.lock`, `Dockerfile:25-27` |
| 44 | 컨테이너: 단일 이미지 + `VARIANT=cpu|gpu`, 비 root(uid 10001), 라벨 6개, HEALTHCHECK | S- | Docker 모범 사례(비 root), OCI 이미지 어노테이션과는 다른 `io.openit.*` 키. 라벨 규칙은 D50. | D22, D50, Dockerfile 주석(레이어 순서, GPU 변형에만 gcc를 넣는 이유). `python:3.12-slim` 베이스 선택 이유는 기록 없음. | `Dockerfile` 전체 |
| 45 | 로깅: 표준 라이브러리 `logging`, 표준 출력, 발화·출력 원문 미기록 | S- | 12-factor 11항(로그를 이벤트 스트림으로 표준 출력에). | 워크스페이스 규칙, `server.py:8` 주석, `tests/test_secrets_hidden.py`. 12-factor를 인용한 곳은 없음. | `server.py:594`, `server.py:413` |
| 46 | 메트릭 | ? | 이 영역에는 메트릭 출력이 없다(src와 boundary.md·runbook.md를 `metrics|prometheus`로 검색, 없음). 계약에 `METRICS.md`가 있으나 이 영역이 거기서 무엇을 져야 하는지는 읽지 않았다. | 확인 못 함 | `grep` 결과 없음, `../boundary-contracts/METRICS.md`(열지 않음) |
| 47 | 시간·난수: 요청 상한은 `time.monotonic`, 평균 지연 계산은 `time.time` | S- | 경과 시간은 단조 시계를 쓰라는 일반 관행(표준 이름은 특정 못 함). | 근거 기록 없음. 평균 지연 계산(`time.time`)은 시계 점프에 약하다(사소). 난수는 쓰지 않는다(mimic은 sha256 결정적 선택). | `server.py:356`, `server.py:364-367` |
| 48 | GPU 세션 키트: `PYTHONPATH` 키트 tar, 최소 I4 읽기 서버(`release_server.py`) 직접 작성, 측정 도구 `measure.py` 직접 작성 | N | 없음. | ADR 0013(대안: conda pip 설치·Docker·모델 복사를 환경 제약으로 기각). I4 참조 서버가 설치 패키지에 없어서 직접 작성했다는 이유가 있다. | ADR 0013 결정 3·6·8, `scripts/gpu_session/` |
| 49 | 파드 준비: uv 0.13.6 고정 + sha256 검증 tarball, 독립 파이썬, root일 때만 apt | S | 공급망 관행(버전 고정 + 해시 검증). apt는 Debian/Ubuntu 패키지 관리. 해시는 같은 릴리스의 `.sha256`에서 옮겼고 릴리스 탈취는 못 잡는다고 인정. | ADR 0014(대안 6개와 기각 이유). RunPod 파드에서 실행은 안 해 봄. | `pod_setup.sh:50-57`, ADR 0014 |
| 50 | 모델 이름의 출처를 프로파일 하나로 두고 불일치 시 시작 전 중단 | S | 공통 규칙("한 사실은 한 곳"). | ADR 0015 | `pod_setup.sh:25-35` |
| 51 | 키트 provenance: `KIT_MANIFEST.txt`에 `git rev-parse HEAD`와 트리 sha256 | S- | 이름 있는 표준 없음(재현성 관행). | ADR 0016 결정 4가 이유를 쓴다. 표준 이름은 없다. | `make_kit.sh:21-25` |

## 분류별 개수

| 분류 | 행 번호 | 개수 |
|---|---|---|
| S | 5, 9, 10, 11, 18, 49, 50 | 7 |
| S- | 7, 13, 16, 21, 43, 44, 45, 47, 51 | 9 |
| N | 2, 4, 20, 25, 26, 29, 31, 33, 34, 35, 36, 37, 38, 40, 48 | 15 |
| N- | 1, 6, 8, 12, 14, 17, 22, 23, 24, 27, 30, 39, 41, 42 | 14 |
| O | 15, 32 | 2 |
| ? | 3, 19, 28, 46 | 4 |
| 합계 | | 51 |

## N-, O, ? 항목만 모음

N-
- 1 vLLM 엔진 선택: 요구 충족 확인만 있고 TGI·llama.cpp·transformers와의 대안 비교는 없다(ADR 0001이 인정).
- 6 한 엔진에 LoRA 둘: 계산상 메모리 절약뿐이고 대안 비교 없음, 동시 적재는 unverified.
- 8 릴리스 보존 4개: 근거 기록 없음.
- 12 `model_runtime_version` MAJOR.MINOR 일치 규칙: SemVer보다 엄격하나 이유 없음.
- 14 표준 라이브러리 HTTP 서버: 선택 당시 비교 기록 없음(ADR 0010이 인정).
- 17 토큰 설정 형식 `역할=토큰`: 형식 선택 이유와 대안 없음.
- 22 요청 총 상한 3.5초: 4초 미만이라는 제약만 있고 값 선택·측정 없음.
- 23 대기 상한 3.0초와 PUT 대기 3.0초: 근거 없음.
- 24 대기 예측식의 초기 평균 0.5초, 이동평균 0.8/0.2: 근거 없음.
- 27 재시도 3회: 근거가 헤드 승인뿐, 측정 없음.
- 30 기타 한도(15s, 32, 2, 16, 5s, 4MiB, 120s, 30s): 초기값, 근거 없음.
- 39 `rtx4090-24g` 프로파일 값: 계산 추정, 0.9·8192·8의 선택 이유 없음, 적재한 적 없음.
- 41 util 0.90→0.85→0.80, 0.05 단위: 지시된 값, 측정 아님.
- 42 로그 문구 정규식 판별: 한 문구만 실기 재현(분류 N-로 둠, 안전 쪽 실패라는 논리는 있음).

O
- 15 연결 상한·소켓 timeout·본문/헤더 검증·깊은 JSON 방어·우아한 종료를 직접 구현: 웹 서버/프레임워크가 제공하는 영역을 직접 만들었고 가져다 쓰지 않은 이유의 비교가 없다.
- 32 Qwen3 채팅 형식 문자열을 직접 조립: 토크나이저의 `apply_chat_template`가 제공하는 것을 직접 만들었고 이유가 없으며, 대조 기준은 4B 하나뿐이다.

?
- 3 제약 디코딩 문법 엔진 xgrammar: 코드는 백엔드를 지정하지 않아 실제로 쓰이는 엔진을 확인하지 못했다. `guided_decoding_backend` 엔진 인자가 vLLM 0.31.0에서 유효한지도 "확인 전"이라고 코드 주석이 적었다.
- 19 자격 발급·보관·갱신·폐기: 이 영역은 받아서 비교만 한다. 교체 절차를 문서에서 찾지 못했다.
- 28 재시도 3회 × 총 상한 3.5초의 합성과 한 턴(extract+respond) 최악 7.0초 대 survey 턴 마감 6초: 계산이 문서에 있는지 못 찾았다.
- 46 메트릭: 영역에 출력이 없고 계약 `METRICS.md`의 요구는 읽지 않았다.

## 점검하지 못한 것

- GPU에서 실행해 확인한 것은 없다(vLLM·xgrammar 동작, 다중 LoRA 동시 적재, 24GB 적재, 지연, 문법 컴파일 시간). 모든 값은 문서에 적힌 대로 인용했다.
- 시험은 돌리지 않았다. 시험 파일이 주장하는 동치성 등(예: `test_anyof_constraint.py`의 1764건)은 읽지 않았고 ADR이 쓴 대로만 옮겼다.
- 비밀 값(토큰, `.env`, `secrets/`)은 읽지 않았다. `runs/`, `build/`는 열지 않았다.
- `README.md`는 5절(시험 상태) 등 전체를 정독하지 않았다. `docs/boundary.md`, `docs/runbook.md`, `docs/GPU_SESSION.md`, `docs/CHANGELOG.md`, `docs/STATUS.md`, `docs/history/`, `docs/change-requests/`는 열지 않았고(필요한 문장은 `grep`/ADR 인용으로 확인), 거기에만 있는 근거는 놓쳤을 수 있다.
- `schema_guard.py`의 변환기 본문(240행 이후), `mimic.py`, `backends.py`의 대부분, `scripts/` 중 `run_session.sh` 대부분, `measure.py`, `prepare_bundle.py`, `release_server.py`, `gpu_*.py`는 읽지 않았다. 해당 요소는 ADR과 머리말 주석으로만 판단했다.
- 헤드 문서 `FAULT_POLICY.md`는 읽기 허용 범위 밖이라 열지 않았다. ADR 0006이 인용하는 원칙 4 등은 계약 `CONVENTIONS.md`에서 읽은 범위로만 확인했다.
- 표준 이름 중 `http.server` 문서의 경고, OWASP LLM 가이드, Docker 모범 사례의 세부는 외부 문서를 이 점검에서 열어 확인하지 않았다. 기억에 의존한 부분은 해당 칸에 그렇게 표시했다.
- 이웃 영역(survey-service, deploy)이 이 영역의 토큰·PUT 역할·ALLOW_FAKE를 실제로 어떻게 쓰는지는 읽지 못했다.
- 다중 릴리스(최대 4개)가 각자 어댑터 쌍을 `max_loras: 2` 엔진에 묶을 때 어댑터 교체 비용과 LoRA 캐시 동작은 확인하지 못했다(vLLM 동작이라 GPU 없이는 확인 불가).
