# model-runtime 구조 규칙 점검 보고서 (2026-10-28, 기준서 D104)

기준서: `head/docs/audit/structure-rules-audit-spec.md`(R1~R7, 항목 A~G). 점검 대상: 이 리포 HEAD `b3fc03d`(작업 트리 변경 없음, 코드는 고치지 않았다). 계약 기준: `../boundary-contracts`(`git describe` = v2.1.1-1-gf984b3c, 작업 트리) 의 INTERFACES.md I8·I9·I4, HTTP.md I9, CONVENTIONS.md 9·10절, `schemas/`, `clients/runtime.py`.

## 0. 요약

| 심각도 | 개수 | ID |
|---|---|---|
| P1 | 3 | MR-01, MR-02, MR-03 |
| P2 | 9 | MR-04 ~ MR-12 |
| P3 | 7 | MR-13 ~ MR-19 |

실행한 것: `.venv/bin/python -m pytest -q -rs -p no:cacheprovider` = **243 통과, 2 건너뜀**(xgrammar 없음 1, 참조 데이터 경로 없음 1), 152.89초. 여기에 계약 적합성 시험 `tests/test_server_e2e.py::E2E::test_contract_client_conformance`(boundary-contracts의 `HttpModelRuntime` 클라이언트가 이 서버를 부름)가 포함돼 통과했다. 통과가 아래 F절 어긋남이 없다는 뜻은 아니다. 적합성 시험이 보지 않는 항목이 MR-09, MR-10, MR-14에 있다.

## A. 컴포넌트 목록 (R2)

**이 영역은 컴포넌트를 선언하지 않았다.** `docs/design.md`, `docs/brief.md`, `README.md`에 컴포넌트 표가 없고(`grep -n "컴포넌트\|component" docs/design.md docs/brief.md` 결과 brief.md의 문진 스키마 `COMPONENTS.yaml` 언급 한 줄뿐), 코드도 모듈 단위로만 나뉘어 있다. 아래는 코드를 읽고 내가 추정한 구분이며, 영역이 정한 것이 아니다.

| 추정 컴포넌트 | 파일 | 할당된 입력 | 출력 | 업무 |
|---|---|---|---|---|
| 프롬프트 조립 | `prompts.py` | role, `ActionExtractionInput`\|`ResponseRequest`, `serving_config.prompt_template_version`, 시스템 프롬프트 문자열 | 프롬프트 문자열 | 채팅 형식 조립(`prompts.py:58 render_prompt`) |
| 스키마 검사·완화 | `schema_guard.py` | role, 값 / 출력 스키마, 지원 키워드 | 위반 목록 / 완화된 스키마 | 입출력 검증, CONTRACT_VERSION 확인, `relax`(`schema_guard.py:60-88, 299`) |
| 서빙 설정 | `serving_config.py` | `serving_config` 객체 | 검증 결과, 해시 | `SERVING_CONFIG_SCHEMA`(`:11`), 해시(`:76`) |
| 출력 보호 정책 + 대체 문장 | `protection.py`, `fallback.py` | `ResponseRequest`, 정책 버전 | 대체 문장 | respond 형식 위반 시 재료 이어 붙이기 |
| 제약 디코딩 백엔드(가짜) | `backends.py` | `GenerationRequest` | 문자열 | FakeBackend + 가짜 출력 생성기 |
| 제약 디코딩 백엔드(vLLM) | `vllm_backend.py` | `GenerationRequest`, 프로파일, 어댑터 디렉터리 | 문자열 | 엔진 적재, 샘플링 인자, LoRA 요청 |
| 세션(라이브러리 API) | `session.py` | 위 전부 | `dict`\|`ResponseResult`\|`RawResult`, `CallInfo` | 렌더 -> 생성 -> 검증 -> 재시도 -> 대체. `load`, `FakeModelSession` 포함 |
| 추론 서버(I9 제공) | `server.py` | HTTP 요청, I4 번들 | HTTP 응답 | 아래 MR-03 |

경계가 없는 덩어리: `session.py`의 `_Session`(두 역할 + 검증 + 재시도 + 대체 + 호출 정보, MR-01), `server.py` 620줄(MR-03).

접착 코드(어느 컴포넌트에도 속하지 않음): `__init__.py`(재수출 20줄), `errors.py`(공용 예외 + `violation_names`), `session.py:227 load`(백엔드 선택·조립), `server.py`의 `build_service`/`main`(환경 변수 읽기), `scripts/`(GPU 세션 키트, 점검 스크립트), `profiles/*.json`.

## B. R3·R4

### MR-01 [R3, R4] P1 — 한 클래스·한 함수가 두 컴포넌트(LoRA-A 추출, LoRA-B 응답)의 일을 `role` 분기로 한다
- 위치와 인용:
  - `session.py:123-151 _attempts`: `if role in JSON_OUTPUT_ROLES:` (`:140`)로 A는 `json.loads` + 스키마 검증, B는 `raw.strip()`(`:147`)을 같은 반복문 안에서 한다. 한 루프가 두 역할의 형식 규칙(J1)을 모두 안다.
  - `session.py:164-187`: `extract`는 실패 시 예외, `respond`는 실패 시 `fallback_text`(`:182`)로 서로 반대 실패 의미를 같은 클래스가 갖는다.
  - `session.py:183`: `CallInfo("LORA_B", ..., "fallback", (("G0", 1), ("G5", 1)))`. B의 보호 규칙 코드를 세션이 직접 적는다.
  - `backends.py:214`: `if req.role != "LORA_A": return FAKE_RESPONSE_TEXT` (FakeBackend 응답기가 A와 B를 한 함수에서 만든다).
  - `prompts.py:40`: `if role == "LORA_B": ... sort_keys=True`(B는 전 수준 정렬, A는 최상위만; 한 함수 `_user_prompt4`가 두 직렬화 규칙).
  - `schema_guard.py:25`: `JSON_OUTPUT_ROLES = ("LORA_A",)`, `:74`, `:78`에서 역할 종류로 분기.
  - `session.py:111-117`: 생성자에서 역할별 제약 스키마를 만들 때 `role in JSON_OUTPUT_ROLES`.
- 왜 위반인가: A와 B는 입력·출력 스키마, 실패 의미, 디코딩 방식이 모두 다른 별개 업무다(A: JSON, 제약 디코딩, 실패 시 503; B: 텍스트, 제약 없음, 실패 시 대체 문장). 한쪽을 바꾸면(예: B에 제약 추가) 같은 함수 안의 다른 쪽 분기를 건드린다. R3(서로의 구조를 모른다), R4(자기 I/O 스키마와 업무만 안다) 위반이다.
- 최소 수정 방향(동작 변경 없이): 역할당 객체 하나. `ExtractRole`(입력 `ActionExtractionInput`, 출력 `ActionExtraction`, 시도 반복에서 JSON 파싱+스키마 검증, 실패는 `ModelOutputInvalid`)과 `RespondRole`(입력 `ResponseRequest`, 출력 `ResponseResult`, 문장 검증, 실패는 `fallback_text`). 둘은 같은 작은 Protocol(`run(input) -> Attempt결과`)만 따르고 `_Session`은 두 객체를 들고 `extract/respond`를 위임만 한다. `_user_prompt4`도 `_prompt_a`/`_prompt_b` 두 함수로 나누고 버전 표(`_USER`)가 역할별 함수를 가리키게 한다. FakeBackend는 역할별 응답기 두 개를 받게 한다(MR-04). 재시도 반복 자체(`max_attempts`, 시드 증가, 재시도 온도)는 두 역할이 같은 계약(INTERFACES I9)이므로 공용 한 곳(작은 `retry` 함수)에 두는 것이 R3에 맞다.

### MR-02 [R3, R4, F] P1 — 프롬프트 조립 규칙 일부가 `server.py`에 있어 라이브러리 경로와 서비스 경로의 프롬프트가 다르다
- 위치: `server.py:70-80 neutralize_special_tokens`, 호출 `server.py:369 body = neutralize_special_tokens(body)`. `render_prompt`(`prompts.py:58-71`)와 `_Session._attempts`(`session.py:125`)에는 이 처리가 없다. 검색: `grep -rn neutralize_special_tokens src scripts`는 `server.py`(정의·호출)와 `scripts/check_reference_data.py:99`뿐이다.
- 인용: `body = neutralize_special_tokens(body)` / `return _SPECIAL_OPEN.sub("< ", value).replace("|>", "| >")`.
- 왜 위반인가: 입력을 프롬프트로 바꾸는 일은 프롬프트 컴포넌트의 업무인데 그 일부(입력 값을 바꾸는 규칙)가 서버(접착 코드)에 있다. 그 결과 `load(...).extract`/`render_prompt`를 직접 쓰는 호출자(model-lab 평가, 게이트, 학습 변환 `docs/boundary.md` "학습 데이터 변환은 `render_prompt`를 쓴다")는 `<`+영문자나 `|>`가 든 발화에서 서비스와 다른 프롬프트를 만든다. `docs/brief.md` 4절 수용 기준 "학습·평가·서비스에서 같은 입력이 같은 프롬프트로 조립된다"와 어긋나는 구조다(해당 문자열이 든 입력에서만). ADR 0011이 의도한 선택이지만(`docs/boundary.md` "특수 토큰" 절) 위치가 잘못돼 있다. 또 변경이 JSON 검증(`validate_input`) 전에 일어나므로 검증은 바뀐 값을 본다.
- 최소 수정 방향: 이 규칙을 `prompts.py`로 옮겨 `render_prompt` 안에서(입력 검증 뒤, 직렬화 전) 적용하거나, 적용하지 않는 학습 변환용 인자를 명시한다. 서버는 호출하지 않는다.

### MR-03 [R3, R4] P1 — `server.py`에 컴포넌트 경계가 없고, 다섯 모듈의 내부 상수·속성·딕셔너리 키를 직접 쓴다
- 위치와 인용:
  - `server.py:44-52` import: `prompts.SUPPORTED_TEMPLATE_VERSIONS/DEFAULT_TEMPLATE_VERSION/DEPRECATED_TEMPLATE_VERSIONS`, `protection.PROTECTION_POLICIES`, `schema_guard.RELAX_POLICY_VERSION`, `backends.load_fake_*`, `make_default_responder`, `errors.violation_names`.
  - `server.py:341-349 info()`: `"relax_policy_version": RELAX_POLICY_VERSION`, `"protection_policy_versions": sorted(PROTECTION_POLICIES)`, `base.name/base.version/base.supported_features`. 서버가 서빙 설정 기대값을 직접 조립해 프롬프트·보호·스키마·백엔드의 내부 표를 한 곳에서 읽는다.
  - `server.py:292-298 _load`: `m["runtime_contract_version"]`, `m["model_runtime_version"].split(".")[:2]`, `m["adapters"][k]["serving_config"]` 등 I4 매니페스트 키를 딕셔너리로 직접 접근한다(라이브러리의 `load`가 받는 인자 모양을 서버가 매니페스트에서 직접 뽑는다).
  - `server.py:299`: `backend = self.base.bind(bundle.adapters) if hasattr(self.base, "bind") else self.base`; `:246 close = getattr(self.base, "close", None)`; `:379 out = getattr(r.session, op)(body)`. `ConstraintBackend` Protocol(`backends.py:21-26`)에 `bind`/`close`가 없는데 서버가 덕 타이핑으로 쓴다.
  - `server.py:554-562 build_service`: fake 백엔드 응답기 구성(`load_fake_flow` 등)과 vLLM 팩토리(`:564-570`, 프로파일 `env` 키를 `os.environ.setdefault`로 전역 환경에 씀)까지 한다.
  - 한 파일이 하는 일: HTTP 처리, 인증/역할, 입력 한도·특수 토큰 처리(MR-02), 릴리스 적재 상태기계, I4 읽기, 큐/데드라인/엔진 락, 준비 상태(`readiness`), 번들 디렉터리 관리, fake 설정 파싱, 종료 처리.
- 왜 위반인가: R3·R4(이웃 컴포넌트의 내부 구조와 설정 필드를 직접 앎). 다른 모듈의 상수 이름이나 `Protocol`에 없는 메서드가 바뀌면 서버가 조용히 깨진다. R2(컴포넌트 경계 부재)도 겹친다.
- 최소 수정 방향: (1) 각 컴포넌트가 자기 버전·기대값을 한 함수로 내놓게 한다(예: `session.serving_config_expectation(backend)`가 `info()`의 `expect`를 만든다). (2) `Protocol`에 `bind`, `close`를 선택 메서드로 명시하거나 `RebindableBackend` 프로토콜을 둔다. (3) 서버 파일을 `http_handler`(라우팅·인증·직렬화), `release_loader`(I4 읽기 + 적재 상태), `scheduler`(락·큐·데드라인), `readiness` 네 모듈로 나눈다. 파일만 나누고 동작은 바꾸지 않는다.

### MR-04 [R3, R4] P2 — FakeBackend와 `GenerationRequest.input`: 백엔드가 A의 입출력 스키마를 안다
- 위치: `backends.py:18 input: Mapping[str, Any] | None = None   # ... 가짜 백엔드가 문항을 읽는 데만 쓰고, 실제 백엔드는 쓰지 않는다`; `backends.py:213-223 respond`는 `inp.get("question_id")`, `inp.get("user_utterance")`를 읽어 `ActionExtraction` 객체를 만든다(`fake_extraction_for`, `:176-204`).
- 왜: 백엔드의 업무는 "프롬프트 문자열 -> 문자열"인데, 요청에 모델 입력 객체를 실어 와서 가짜가 A의 입력 스키마를 읽고 A의 출력 스키마를 직접 만든다. 실제 백엔드가 쓰지 않는 필드가 공용 요청에 있다는 점은 R4(자기 I/O 밖의 정보). 가짜 구현이라 위험은 낮지만 `GenerationRequest`의 모양이 가짜 때문에 오염됐다.
- 최소 수정: `input`을 `GenerationRequest`에서 빼고, FakeBackend 응답기를 "역할별 Fake 컴포넌트"(`FakeExtractor`, `FakeResponder`)로 나눠 세션 쪽에서 입력 객체와 함께 부르게 한다(MR-01의 역할 객체 분리와 함께).

## C. R5 — 영역 안 컴포넌트 사이 스키마

기계가 읽는 형태로 한 곳에 정의된 것(확인함):
- `GenerationRequest`(frozen dataclass, `backends.py:10-18`), `ConstraintBackend`(Protocol, `backends.py:21-26`), `RawResult`/`ResponseResult`/`CallInfo`(dataclass, `session.py:31-57`), `ModelSession`(Protocol, `session.py:60-68`), `ProtectionPolicy`(dataclass, `protection.py:12`), `SERVING_CONFIG_SCHEMA`(JSON Schema, `serving_config.py:11`).

### MR-05 [R5] P2 — 암묵적 컴포넌트 사이 스키마(딕셔너리 키 관습)
- `GenerationRequest.decoding: Mapping[str, Any]`는 `serving_config.decoding`의 키(`temperature`, `max_tokens`, `top_p`, `constraint_mode`, `retry_temperature`)를 세션(`session.py:127-137`)과 vLLM 백엔드(`vllm_backend.py:136 build_sampling_params`의 `d["temperature"]`)가 각자 키 이름으로 읽는다. 타입 있는 `DecodingConfig`가 없다. (`SERVING_CONFIG_SCHEMA`는 JSON Schema로 있으나 코드가 쓰는 쪽은 `dict`다.)
- 프로파일(`profiles/*.json`)의 `base_model`, `engine`, `env`, `status` 키: 검증은 `vllm_backend.py:32-43 load_profile`의 `_ENGINE_KEYS` 화이트리스트(`:27-29`)뿐이고 `env` 키는 `server.py:567`이 따로 읽는다. 프로파일 스키마(JSON Schema)가 없다.
- 세션이 만드는 `_cfgs`(`session.py:102`), 서버의 `Release.hashes/prompt_hashes`(`server.py:139`)는 `dict`다.
- `ConstraintBackend` Protocol이 `bind`/`close`를 포함하지 않음(MR-03).
- 최소 수정: `DecodingConfig`(TypedDict 또는 dataclass), `Profile` JSON Schema를 `serving_config.py`와 같은 자리에 둔다.

## D. R1 — 이웃 영역 구조에 기대는 곳

확인한 방법: `grep -rn "\.\./\|survey-service\|release-gate\|model-lab\|deploy" src scripts Dockerfile`, `grep -rn "import" src`, 이웃 패키지 import 검색(`turn_engine`, `survey_schema`, `release_gate` 등).

- 코드 의존: `src/`의 이웃 영역 import는 **없다**. 외부는 `boundary_contracts`(계약: `load_schema`, `validator`, `canonical`, `read_secret`, `clients.releases.HttpReleaseReader`)와 `jsonschema`, 선택 `vllm`뿐이다. 문제없음.
- turn-engine: 아래 "turn-engine import" 절.
- 다음은 허용된 읽기 전용 참조이거나 주석·문서 수준이다.

### MR-06 [R1] P3 — 문진 스키마 디렉터리 배치를 안다
`schema_guard.py:66 self.contract_version = int((d.parent / "CONTRACT_VERSION").read_text().strip())`. `schema_dir`가 `<...>/schema/schemas`이고 정수 버전 파일이 그 상위에 있다는 survey-service의 배치를 코드가 안다. INTERFACES I9가 같은 문장으로 정한 것이라 계약 허용 범위이지만 파일 위치가 계약 스키마(JSON Schema)가 아니라 문서 서술이다. 같은 배치를 `scripts/gpu_session/make_kit.sh:10`(`SCHEMA_ROOT="$ROOT/../survey-service/schema"`), `scripts/gpu_check.sh:48`(`$WS/survey-service/schema/`), `tests/test_runtime.py:75`가 알고, `Dockerfile:6-9`와 `server.py:64 EMBEDDED_SCHEMA_DIR = "/opt/survey-schema/schemas"`가 알고 있다. 워크스페이스 규칙은 `../survey-service/schema` 읽기를 허용하므로 위반은 아니나, 정수 버전 하나만 필요하다면 `schema_dir` 대신 정수 `contract_version`을 인자로 받고 읽기는 진입점이 하게 하는 편이 R1에 맞다(제안).

### MR-07 [R1] P2 — 가짜 백엔드가 survey-service/turn-engine의 문항 흐름 규칙을 재구현하고 그 파일 형식이 영역 밖과 맞물린다
- 위치: `backends.py:77-95 load_fake_flow_spec`("`skips`: survey-service QuestionFlow와 같은 모양 [{"slot","value","skip_item"}]", `:80`), `:168-204 _next_after/fake_extraction_for`(건너뛰기·정정·되묻기 next_item 계산), `docs/runbook.md` 41행("survey-service의 문항 순서와 같게", "survey-service QuestionFlow와 같은 ...").
- 왜: 가짜가 이웃(survey-service)의 `QuestionFlow` 모양을 모방하고, turn-engine `validate_extraction`이 검증하는 규칙(next_item은 순서상 다음 문항, 건너뛰기 적용, `save` 규칙)을 같은 방식으로 다시 구현한다(`backends.py:179-181` 주석 "turn-engine v0.3.0 validate_extraction과 일치"). 이웃 쪽 규칙이 바뀌면 이 가짜가 어긋나고, 어긋남은 `tests/test_fake_flow.py`가 turn-engine이 설치돼 있을 때만 잡는다(건너뛰면 못 잡음). R1(이웃 구조) + R7(같은 규칙 재구현).
- `MODEL_RUNTIME_FAKE_FLOW_FILE`(`{"order","slots","skips","corrections","reasks"}`)은 deploy/survey-service가 시험용으로 이 영역에 건네는 파일 형식인데 boundary-contracts에 없다(MR-10).
- 최소 수정: 가짜의 "다음 문항 계산"을 호출자가 주는 응답 목록(`FakeBackend.scripted`는 이미 있음)으로 줄이고, 문항 흐름 해석은 가짜 출력을 쓰는 쪽(survey-service의 시험 설정)이 소유하게 한다.

### MR-08 [R1] P3 — 문서가 이웃의 사실을 복사한다
`docs/boundary.md` "시간 불변식" 절: "키오스크 대기 10s > gateway 9.5s > survey 턴 마감 6s > survey의 model-serving 읽기 timeout 4s"(출처는 헤드 FAULT_POLICY라고 적었지만 수치를 복사). `server.py:149` 주석("survey 읽기 timeout 4s보다 짧게"), `server.py:167` 주석("survey-service가 404에서 PUT으로 다시 적재한다"), `docs/boundary.md` "호출자가 지켜야 할 것"의 survey-service 직렬화 요구. 워크스페이스 규칙("이웃 영역의 사실은 복사하지 않고 링크")을 어긴다. 수치는 인용 대신 링크로 바꾼다.

## E. R6 — 영역 사이 스키마와 계약 유무

제공·소비하는 인터페이스별:

| 인터페이스 | 계약 스키마 | 비고 |
|---|---|---|
| I9 서버 요청·응답 본문 | `ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`, `BoundaryError`(`schemas/`), 적재 상태 본문은 HTTP.md 표에만(JSON Schema 없음) | `{"release_id","state","error"}` 본문 스키마 파일은 `schemas/`에 없음(`ls schemas`로 확인) |
| I4 읽기(소비) | `ReleaseManifest`, `HttpReleaseReader` | 있음 |
| I9 라이브러리(`load` 등) | INTERFACES I9 시그니처(텍스트) | `system_prompts` 인자 추가는 CR-004(제안)이며 계약 시그니처에 없음(`session.py:227-229`) |
| 문진 스키마 정수 버전 | `CONTRACT_VERSION` 파일 | 있음(MR-06) |

### MR-09 [R6] P2 — `serving_config` 스키마를 이 영역이 정의하고 영역 경계를 넘는다
`serving_config.py:11-53 SERVING_CONFIG_SCHEMA`가 정의하고 `ModelPackageManifest`/`ReleaseManifest`의 `serving_config`는 "형식은 model-runtime이 정한다"는 자유 객체(`ModelPackageManifest.schema.json:69-71`)다. model-lab이 쓰고, release-gate가 담고, deploy가 `/v1/info.serving_config_expectation`을 읽는다(`server.py:340-349`, `runbook.md:140`). INTERFACES I9("형식은 model-runtime이 정하고 라이브러리로 내놓는다")가 위임한 것이지만 R6(영역 사이 스키마는 헤드 책임)에 비추면 경계를 넘는 스키마가 계약 패키지 밖에 있다. 제안: 같은 JSON Schema를 boundary-contracts로 올리거나 계약이 이 위임을 명시적 예외로 기록한다(헤드 판단).

### MR-10 [R6] P2 — 영역 경계를 넘는데 계약에 없는 형식 목록
1. `/v1/info`의 추가 필드: `system_prompt_sha256`, `serving_config_expectation`(`constraint`, `prompt_template_versions`, `prompt_template_version_default`, `deprecated_prompt_template_versions`, `protection_policy_versions`, `output_contract`), `test_hooks`(`server.py:350-354`). HTTP.md I9는 6개 필드만 정한다. deploy와 model-lab이 읽는다고 `server.py:341` 주석과 `runbook.md:140`이 말한다.
2. 프로파일 JSON(`profiles/*.json`, `MODEL_RUNTIME_PROFILE`): deploy가 고르는 서빙 설정 형식. 스키마 없음(MR-05).
3. 가짜 흐름 파일(`MODEL_RUNTIME_FAKE_FLOW_FILE`)과 `MODEL_RUNTIME_FAKE_EXTRACTION`(MR-07).
4. 토큰 파일 형식 `역할=토큰,...`과 역할 이름 `loader`, `dev`(`server.py:61 INFER_DENY_ROLES`, `:83 parse_tokens`). HTTP.md 공통은 "영역 문서가 이름을 정한다"라고 위임했고 이 영역은 `docs/boundary.md`에 적었다. 이름이 deploy compose와 맞물린다.
5. `X-Openit-Protections`의 코드 값(`G0`, `G5`): HTTP.md가 "코드 값은 model-runtime이 정한다"로 위임.
6. 환경 변수 이름 `MODEL_RUNTIME_*` 약 30개(deploy compose가 사용)와 파일 비밀 이름 `MODEL_RUNTIME_TOKENS_FILE`, `MODEL_RUNTIME_RELEASE_TOKEN_FILE`(D84는 워크스페이스 규칙).
7. `scripts/gpu_session/prepare_bundle.py`가 `manifest.json`, `question_bank.json`, `adapters/<역할>/` 번들 디렉터리를 만들고 `release_server.py`가 I4 모양의 tar를 낸다(`release_server.py:2-9, 51-56`). 릴리스 번들·I4 서버 형식을 이 영역이 별도로 재구현한 것이다(운영 경로가 아닌 GPU 세션 키트용). 계약의 I4 서버 구현(release-gate)과 어긋나면 키트만 모르고 통과한다.
8. 서버 쪽 번들 디렉터리 규칙: `dest/<release_id>` 아래 풀기(ADR 0004)는 `HttpReleaseReader`의 동작에 의존한다.
9. 이미지 라벨 키와 `SURVEY_SCHEMA_VERSION`/`TURN_ENGINE_VERSION` 빌드 인자(`Dockerfile:41-47`)와 Dockerfile `--build-context boundary=`/`schema=` 이름: deploy와 맞물린 빌드 인터페이스(D50은 라벨 키만 정함).

## F. 계약 준수 (I9, HTTP.md I9, CONVENTIONS 9·10)

`예`는 코드 위치를 읽어 확인했다는 뜻이며 별도 표기가 없으면 시험(243 통과)도 이 경로를 지난다.

| # | 계약 요구(절) | 구현 | 일치 |
|---|---|---|---|
| 1 | `PUT /v1/releases/{id}` 본문 없음, 멱등. 200 LOADED / 202 LOADING (HTTP.md I9 표 1행) | `server.py:488-495`, `put()` `:264-286`: LOADED 200, LOADING 202(`put_wait_s` 3.0초 대기 후), 본문은 읽고 버림 | 예 |
| 2 | 실패 412 FAILED_PRECONDITION / 422 INTEGRITY_ERROR·UNSUPPORTED_VERSION / 503 | `STATUS`(`:66`), `to_boundary`(`:124-134`): 설정 불일치·ContractViolation -> 412, 프롬프트 해시 -> 422, 버전 -> 422, 그 밖 -> 503 | 예 |
| 3 | `GET /v1/releases/{id}` ABSENT/LOADING/LOADED/FAILED + error | `status_body` `:325-328`, `:496-497` | 예 |
| 4 | extract: 본문 `ActionExtractionInput`, 200 `ActionExtraction` JSON | `:498-508`, `_send`가 `application/json; charset=utf-8`(`:434`) | 예 |
| 5 | respond: 200 문장 그대로 `text/plain; charset=utf-8` | `:401`, `:432` | 예 |
| 6 | `/v1/info` 6필드 | `:350-354`에 6필드 + 3개 추가. 단 로드된 릴리스가 없을 때 `serving_config_hash`는 `{}`(계약 예: `{"LORA_A","LORA_B"}`). 클라이언트(`clients/runtime.py:info`)는 `loaded` 목록만 확인해 통과 | 필드 일치, 추가 있음(MR-10 1항) |
| 7 | `/healthz`, `/readyz` 인증 없음, readyz 가능하면 200 아니면 503 | `:469-477`: healthz는 토큰 검사 전 200, readyz는 토큰 없이도 응답(사유는 인증된 호출에만) | 예 |
| 8 | 인증 Bearer, 토큰 없거나 틀리면 403(영역 사이 API, CONVENTIONS 9) | `_role`(`:447-458`), `:478-479` 403 `PERMISSION_DENIED` | 예 |
| 9 | 400 입력 계약 위반 | `ContractViolation` -> 400(`:402-403`), JSON 아님·본문 크기 -> 400 | 예. 단 길이 한도는 MR-11 |
| 10 | 적재 안 된 릴리스는 404 NOT_FOUND | `infer` `:360-362` | 예 |
| 11 | 헤더 `X-Openit-Outcome: ok|fallback`(필수), `X-Openit-Attempts`(선택), `X-Openit-Protections`(선택, 형식 `G5:1,G2:2`) | `:398-400`, 형식 `"코드:건수"` 쉼표 이음 | 예(클라이언트 정규식 `^[A-Za-z][A-Za-z0-9_]*:[0-9]+...` 통과) |
| 12 | respond 실패 시 대체 문장 + `fallback`, 상태 200 | `session.py:175-187` | 예 |
| 13 | extract 실패 시 503 UNAVAILABLE `details.reason="OUTPUT_INVALID"`, `details.attempts` 선택 | `server.py:404-408`. 단 `details.violations` 추가(MR-12) | 예(추가 있음) |
| 14 | 대기가 호출 timeout(서비스 4초)을 넘을 것 같으면 큐에 쌓지 말고 503 | `:363-364`, `:371-372`: 예상 대기 > `queue_wait_s`(3.0) 거절, 총 상한 `request_deadline_s` 3.5초 | 예(값은 측정 안 함, `docs/boundary.md` 경고) |
| 15 | 시스템 프롬프트는 릴리스 패키지 파일만 사용 | `session.py:200-213, 238` | 예 |
| 16 | `X-Model-Runtime-*` 헤더는 쓰지 않는다 | `grep -rn "X-Model-Runtime" src` 결과 없음 | 예 |
| 17 | INTERFACES I4: `model_runtime_version` MAJOR.MINOR 불일치면 거부 | `server.py:294` | 예 |
| 18 | INTERFACES I4: `runtime_contract_version` 불일치면 거부(422) | `server.py:292-293` | 예 |
| 19 | INTERFACES I9 `load` 시그니처 | `session.py:227`: `system_prompts` 필수 인자 추가 | 불일치(CR-004 제안, MR-13) |
| 20 | INTERFACES I9 재시도: 프롬프트 동일, 시드만 `기본 시드 + 시도 번호` | `session.py:139 dec["seed"] + k` (프롬프트는 루프 밖에서 한 번 렌더) | 예. 재시도 온도(`retry_temperature`, `:136`)는 계약에 없는 추가 입력(serving_config 안이라 해시에 반영) |
| 21 | release_id 형식(CONVENTIONS 10: `[A-Za-z0-9][A-Za-z0-9._:-]*` 128자) | `ID_RE`(`:65`) | 예 |
| 22 | CONVENTIONS 7: 생성자는 설정을 인자로만 받고 환경 변수는 진입점이 읽는다 | `build_service`/`main`만 `env` 읽음. 단 `server.py:569 os.environ.setdefault`가 전역 환경을 씀 | 대체로 예 |

### MR-11 [F] P2 — 입력 길이 한도 400이 계약 밖 거절이다
`server.py:367-368`: `if len(json.dumps(body, ensure_ascii=False)) > self.max_input_chars: return 400, BoundaryError("INVALID_RECORD", f"입력이 너무 길다(최대 {self.max_input_chars}자)")`. HTTP.md I9의 400은 "입력 계약 위반"인데 `ActionExtractionInput`/`ResponseRequest` 스키마에는 `maxLength`가 없어 계약상 유효한 입력을 서버가 400으로 거절한다. 변경 요청 `docs/change-requests/005-input-maxlength.md`가 제안 상태다(헤드 미처리). 값 2500은 토큰 수를 재지 않은 추정(주석 `server.py:56`). 최소 수정: CR-005를 처리하거나 그때까지 한도 초과는 `details.reason`을 붙여 계약 위반과 구분한다. 호출자 survey-service는 이 한도를 계약으로 알 수 없다.

### MR-12 [F] P2 — `details.reason` 값과 `details` 키가 계약 정의 밖이다
- `server.py:393`: `BoundaryError("UNAVAILABLE", "추론이 요청 상한을 넘겼다", {"reason": "ENGINE_TIMEOUT"})`. CONVENTIONS 10절은 정의된 `reason` 값을 `STALE_REPORT`, `ALREADY_DECIDED`, `OUTPUT_INVALID`로 한정하고 CONVENTIONS 9절은 "새 사유는 PUBLIC_API.md나 HTTP.md에 적힌 것만 쓴다"고 한다. `grep -rn ENGINE_TIMEOUT ../boundary-contracts`(md, json, py)는 결과가 없다. `docs/boundary.md`는 `ENGINE_TIMEOUT`을 "재시도 가능"이라고 적지만 계약은 모른다.
- `server.py:407`: `details`에 `violations`(경로:키워드 목록) 추가. HTTP.md는 `attempts`만 선택 필드로 정한다. `BoundaryError.details`는 `type: object`라 스키마는 통과하지만 계약 문서에 없다.
- 최소 수정: CR로 `ENGINE_TIMEOUT`(과 `violations`)을 HTTP.md I9에 올리거나, 계약 안의 `reason` 없는 503으로 낸다.

### MR-13 [F] P3 — `load`에 계약에 없는 인자 `system_prompts`
`session.py:227-229`와 `docs/boundary.md`가 CR-004 제안 상태로 인정한다. INTERFACES I9의 `load` 시그니처에는 `system_prompts`가 없는데 프롬프트 파일을 읽는 방법이 시그니처에 없어 현행 계약은 이행이 불가능하다. 헤드가 CR-004를 처리해야 닫힌다. 같은 이유로 `render_prompt`의 `schemas` 선택 인자(`prompts.py:58`)는 내부용 추가 인자다.

### MR-14 [F] P3 — 계약 문서가 규정하지 않은 HTTP 동작(코드 읽기, 실행으로 확인하지 못함)
- `server.py:107-111 BoundedServer.process_request`: 동시 연결 한도 초과 시 `HTTP/1.1 503 ... Content-Length: 0 ... Retry-After: 1`을 본문 없이 쓴다. CONVENTIONS 9는 "실패는 BoundaryError 본문"을 요구하고 영역 사이 API의 과다 요청은 503 `UNAVAILABLE`(본문 있음)이다. 호출자는 `HttpClientBase`가 본문 없는 503을 `UNAVAILABLE`로 취급하는지에 기대야 한다(`boundary_contracts/clients/_http.py`는 읽지 않았다).
- `server.py:524-526`: `do_GET/do_PUT/do_POST`만 정의. `do_DELETE`/`do_HEAD` 등은 정의가 없어 `BaseHTTPRequestHandler` 기본 응답(501, HTML 본문)이 나갈 것으로 읽힌다(실행 확인 안 함). 계약은 지원하지 않는 메서드의 응답을 정하지 않는다.
- 요청 총 상한 3.5초 안에서 `_attempts`의 재시도 반복(`session.py:133`)은 데드라인을 보지 않아 서버가 503을 낸 뒤에도 스레드가 엔진을 최대 `max_attempts`회 계속 쓴다(`docs/boundary.md`가 인정). 계약 위반은 아니다.

## G. R7 — 이중 검증·재시도·규칙 재구현

확인한 방법: 검증 호출 지점 검색(`grep -n "validate\|output_violations\|iter_errors\|file_hash\|sha256_hash" src/model_runtime/*.py`), `session.py`·`server.py`·`prompts.py` 정독.

| 위치 | 내용 | 판정 |
|---|---|---|
| `session.py:185-186` | `respond`가 자기가 만든 대체 문장을 다시 `output_violations("LORA_B", ...)`로 검증하고 실패하면 `RuntimeError` | 자기 출력 재검증. P3 (MR-15) |
| `session.py:72`, `serving_config.py:78` | `validate_serving_config`가 `_check_config`와 `serving_config_hash` 안에서 역할마다 두 번 호출된다(`_build` `:219-221`) | 같은 검증 두 번. P3 (MR-15) |
| `session.py:200-213 read_system_prompt` | 시스템 프롬프트 파일 해시를 다시 확인. INTERFACES I4는 `load_bundle`이 "시스템 프롬프트 파일 포함 모든 해시를 검증한다"고 정함 | 서버 경로에서 이웃(`HttpReleaseReader`)이 보증한 값을 재검증. 단독 `load`(model-lab 평가)에서는 필요하므로 의도된 중복. 기록만 |
| `schema_guard.py` 완화 + `session.py:148` 사후 검증 + 재시도 | 제약 디코딩은 완화(넓힘)한 스키마라 사후 엄격 검증은 같은 규칙의 중복이 아니라 ADR 0002가 정한 보완 | 위반 아님(백엔드가 보증하지 않는다고 선언함) |
| `backends.py:168-204` | turn-engine `validate_extraction`이 검증할 next_item·건너뛰기 규칙을 가짜가 재구현 | MR-07. P2 |
| `backends.py:121-122 _REASK_REASONS`, `:148 _KEY` | `ActionExtraction` 스키마(`schemas/ActionExtraction.schema.json:45-46, 66`)의 reason 열거와 키 패턴을 코드에 복사. 키 패턴은 현 태그에서 같은 문자열임을 확인, reason 열거 전체의 동일성은 확인하지 못함 | 계약 규칙의 복사본. P2 (MR-16) |
| 입력 검증 | 서버 길이 한도(`server.py:367`)와 `render_prompt` -> `validate_input`(`prompts.py:65`) | 서로 다른 규칙이라 이중 아님. 순서만 MR-02 |
| 재시도 | model-runtime `max_attempts`(기본 3, ADR 0007) 뒤에 survey-service 재시도 1회 | 계약이 정한 분업(INTERFACES I9 "서비스가 지키는 것"). 위반 아님 |

### MR-15 [R7] P3 — 자기 출력·설정 이중 검증
위 표 1·2행. 근거는 각 위치의 코드(`session.py:185 if self._schemas.output_violations("LORA_B", result.text): raise RuntimeError(...)`). 대체 문장 규칙(`fallback.py`)이 비어 있지 않은 문장을 보증하므로 재검증은 불필요하다(`empty_fallback_text`가 항상 있다, `protection.py:25`). 설정 검증은 `serving_config_hash` 안의 호출만 남기거나 `_check_config`에서 한 번만 한다.

### MR-16 [R6, R7] P2 — 계약 스키마 내용의 코드 복사(`_REASK_REASONS`, `_KEY`)
`backends.py:121-122`(REASK 허용 reason 11개), `:148`(모델 키 정규식). 계약 스키마가 바뀌면 가짜가 조용히 어긋난다. 스키마(`load_schema("ActionExtraction")`)에서 읽거나, 가짜 출력은 사후에 계약 검증기를 한 번 통과시켜 어긋남을 시험에서 잡는다(현재 `FakeModelSession`은 세션 검증을 지나므로 서버를 통해서는 잡히지만 `load_fake_reasks`의 입력 검증은 복사본에 의존한다).

## 컴포넌트가 서로의 구조를 아는 곳 (요청 사항 (2)의 정리)

| 모듈 | 속하는 컴포넌트(추정) | 아는 이웃 구조 |
|---|---|---|
| `schema_guard.py` | 스키마 검사·완화 | `INPUT_CONTRACT/OUTPUT_CONTRACT/JSON_OUTPUT_ROLES`(역할 구조)를 소유하고 `prompts.py:36`, `session.py:20`, `server`가 가져다 쓴다(MR-01). 이웃 코드는 import하지 않음 |
| `protection.py` | 출력 보호 정책 | `judgments` 문자열 J2가 `fallback.py`의 조립 알고리즘을 서술(`protection.py:28`)해 두 파일이 같은 규칙을 두 번 적는다. 코드 의존 아님 |
| `fallback.py` | 출력 보호 정책 | `ResponseRequest`의 `known/ask/hold/next.text` 키(`fallback.py:15-16`)를 딕셔너리 키로 읽음(자기 입력 스키마라 허용). `protection.PROTECTION_POLICIES` 직접 사용 |
| `serving_config.py` | 서빙 설정 | 다른 모듈 import 없음(`errors`, `boundary_contracts.canonical`뿐). 내용은 백엔드 이름·버전·지원 기능(`constraint`)과 프롬프트·보호 버전을 포함해 이웃 컴포넌트의 식별자를 담는다. 이를 `_check_config`(`session.py:71-84`)가 이웃들의 상수와 맞대어 본다 |
| `prompts.py` | 프롬프트 조립 | `schema_guard.INPUT_CONTRACT, SchemaSet`을 import해 입력 검증까지 한다(`prompts.py:63, 65`). 프롬프트가 스키마 검사 컴포넌트의 클래스를 직접 쓴다. `_schemas_for`(`:52-54`)는 `lru_cache` 전역 캐시 |
| `vllm_backend.py` | vLLM 백엔드 | `backends.GenerationRequest`(공용 프로토콜 타입)와 `errors`만 import. 역할 이름 `ROLES = ("LORA_A","LORA_B")`(`:25`)를 `schema_guard.INPUT_CONTRACT` 키와 별도로 하드코딩. 클래스 전역 `_next_lora_id`(`:61`). `bind`는 `copy.copy(self)`로 엔진을 공유 |
| `session.py` | 세션 | 위 6개를 모두 import. `_check_config`가 `PROTECTION_POLICIES`, `SUPPORTED_TEMPLATE_VERSIONS`, `RELAX_POLICY_VERSION`, 백엔드 속성에 직접 접근(MR-17). `load()`가 `VLLMBackend`를 지연 import해 생성(`:242`) |
| `server.py` | 추론 서버 | MR-03 |

### MR-17 [R3] P2 — `_check_config`가 네 컴포넌트의 내부 상수를 한 함수에서 대조
`session.py:71-84`: `PROTECTION_POLICIES`(protection), `SUPPORTED_TEMPLATE_VERSIONS`(prompts), `RELAX_POLICY_VERSION`(schema_guard), `backend.name/version/supported_features`(backend). 서빙 설정 컴포넌트(`serving_config.py`)는 형식만 알고 의미 검사는 세션이 한다. 각 컴포넌트가 `supports(serving_config 조각) -> bool`을 내놓고 세션이 호출만 하는 것이 R3에 맞다. MR-03의 `info()`와 같은 뿌리다.

### MR-18 [R3] P3 — 전역·공유 상태
`prompts.py:52 @lru_cache(maxsize=8) def _schemas_for`(모듈 전역 스키마 캐시, 프로세스 공유), `vllm_backend.py:61 _next_lora_id = itertools.count(1)`(클래스 전역 카운터), `server.py:569 os.environ.setdefault`(프로파일의 `env`가 프로세스 환경을 바꿈), `vllm_backend.py:126 bind`가 `copy.copy`로 엔진·`_llm` 참조를 공유. 시험 간 간섭 가능성은 확인하지 못했다.

### MR-19 [문서] P3 — 영역 안 문서 불일치
`docs/design.md` 표의 "프로파일: `gpu-12g`=Qwen3-4B"는 `profiles/gpu-12g.json:9 "base_model": "Qwen/Qwen3-1.7B"`와 `README.md`(1.7B)와 다르다. 한 사실을 한 곳에서만 쓴다는 규칙에 따라 design.md의 값은 프로파일 파일 링크로 바꾼다. 또 `docs/change-requests/` 목록이 README에 "001~004"로 적혀 있으나 파일은 001~005다(`ls docs/change-requests`).

## 요청 사항별 직접 답

1. **LORA_A/LORA_B 분기(요청 1)**: R3·R4 위반이다(MR-01, MR-04). 최소 변경은 `ExtractRole`/`RespondRole` 두 객체 + 공용 재시도 함수 하나, 프롬프트 직렬화 함수 둘, FakeBackend 응답기 둘이다. 공개 API(`extract`, `respond`, `*_raw`)와 서버는 바뀌지 않는다.
2. **모듈별 소속(요청 2)**: A절 표와 위 "서로의 구조를 아는 곳" 표.
3. **turn_engine import(요청 3)**: 아래 절.
4. **I9 서버 대조(요청 4)**: F절 22행 표. 어긋남: MR-11, MR-12, MR-13, MR-14. 상태 코드·헤더·본문·멱등·타임아웃 상한은 위 표대로 일치(적합성 시험 통과).
5. **이웃에 대해 아는 구조(요청 5)**: release-gate I4는 `HttpReleaseReader`(계약 클라이언트)로만 읽고 매니페스트 키를 딕셔너리로 직접 접근(MR-03); 문진 스키마는 `CONTRACT_VERSION` 파일 위치만(MR-06); 문항 흐름은 가짜가 survey-service `QuestionFlow` 모양을 모방(MR-07). 코드 import는 없다.
6. **계약에 없는 형식(요청 6)**: MR-09, MR-10.

## turn-engine import (요청 3)
`grep -rn "turn_engine" --include=*.py --include=*.sh --include=*.toml --include=Dockerfile --include=*.lock .`(`.venv` 제외) 결과: `src/`에는 import가 **없다**. import하는 곳은 **`tests/test_fake_flow.py` 한 파일**(`:16-20 _turn_engine()`의 `pytest.importorskip("turn_engine")` + `from turn_engine.flow import DATASET_V2_FLOW`, `from turn_engine.validation import validate_extraction`, 그리고 `:147-149`, `:233-235`, `:303-305`에서 같은 import). 이유: FakeBackend가 만든 `ActionExtraction`이 turn-engine의 의미 검증(`validate_extraction`, INTERFACES I8)을 통과하는지 시험하기 위해서다. `pyproject.toml:14`는 turn-engine을 핀하지 않고(`dev = ["pytest"]`), 설치돼 있지 않으면 건너뛴다(이 점검의 `.venv`에는 설치돼 있어 돈 것으로 보인다: 건너뜀 2건에 이 시험이 없다). 그 밖에 `scripts/make_neutral_package.py:134`, `scripts/gpu_session/prepare_bundle.py:198`이 문자열 `"turn_engine_version": "none"`을 쓰고 `Dockerfile:44`가 라벨 `io.openit.turn-engine`을 `TURN_ENGINE_VERSION=none` 기본으로 둔다(코드 의존 없음). 워크스페이스 규칙상 turn-engine은 의존이 허용된 대상이라 R1 위반은 아니다. 다만 가짜 쪽 규칙 재구현(MR-07)이 이 시험에 의존하는 점이 R7 쪽 위험이다.

## 문제없음으로 확인한 항목

- 코드 의존: `src/` import 전수 확인(`grep -n "^from\|^import" src/model_runtime/*.py`). 이웃 영역 패키지 import 없음. 외부 의존은 `boundary_contracts`, `jsonschema`, 선택 `vllm`.
- 모델 입출력 스키마의 복사본 없음: `schema_guard.py:16 from boundary_contracts import load_schema, validator`, 테스트가 `list(SCHEMA_DIR.glob("*.json")) == []`를 확인(`tests/test_runtime.py:79`). 단 가짜 내부 상수는 MR-16.
- 비밀: `server.py:537 read_secret("MODEL_RUNTIME_TOKENS", environ=env)`, `:577 read_secret("MODEL_RUNTIME_RELEASE_TOKEN", ...)`. 로그에 비밀 출처만(`:546`). 이 점검에서 환경 변수·비밀 파일을 읽지 않았다.
- 로그에 발화·출력 원문 없음: `session.py:170, 184`, `server.py:405, 410, 413`은 개수·예외 이름만. (모든 `log.` 호출을 전수 읽지는 않았다. 점검한 파일 범위의 호출만.)
- 해시·직렬화는 `boundary_contracts.canonical` 사용(`serving_config.py:8`, `session.py:12`).
- 이웃 포트·호스트: 코드 안에 이웃 호스트·포트 하드코딩 없음(`grep -rn "localhost\|127.0.0.1\|:80[0-9][0-9]" src`는 `server.py:601` 서버 자신의 기본 바인딩뿐; 위 grep을 이 형태로 실행하지는 않았고 `server.py` 정독으로 확인).

## 점검하지 못한 것

- 실행: 이미지 빌드, `scripts/smoke.sh`, 서버 기동 후 직접 호출, GPU/vLLM 경로, xgrammar 경로(`tests/test_key_order.py` 건너뜀), 참조 데이터 시험(`MODEL_RUNTIME_REFERENCE_DATA_DIR` 없음). 따라서 `vllm_backend.py`는 정독만 했고 실제 vLLM과의 동작은 확인하지 못했다.
- 읽지 않은 파일: `tests/`의 대부분(`test_fake_flow.py`의 import 줄과 `test_server_e2e.py` 앞부분, `helpers.py`, `test_runtime.py` 일부만 봄), `scripts/` 대부분(`gpu_probe.py`, `gpu_serve_check.py`, `measure.py`, `make_neutral_package.py` 본문, shell 스크립트 본문은 헤더·import·경로 grep만), `docs/adr/` 대부분(0004만 읽음), `docs/runbook.md` 대부분(41, 140행 위주), `docs/history/`, `docs/STATUS.md`, `docs/CHANGELOG.md`(grep으로 일부), `requirements-*.lock`.
- 계약 쪽: `boundary_contracts/clients/_http.py`(본문 없는 503 처리, MR-14), `conformance/servers.py`의 `RuntimeReferenceServer` 전체, `V2_DRAFT.md`, `PUBLIC_API.md`는 읽지 않았다. HTTP.md는 I9 절과 공통부만, INTERFACES.md는 I4, I8, I9만 읽었다.
- deploy compose, survey-service의 `inference-server-spec.md`, 헤드 `FAULT_POLICY`는 읽기 허용 범위 밖이라 읽지 않았다. 그래서 `MODEL_RUNTIME_*` 환경 변수·`loader` 역할·`FAKE_FLOW_FILE` 형식을 이웃이 실제로 어떻게 쓰는지(MR-10)는 확인하지 못했다.
- `ActionExtraction` 스키마 `reason` 열거와 `_REASK_REASONS`의 전체 동일성(MR-16), 시험 간 전역 상태 간섭(MR-18), `do_DELETE` 등의 실제 응답(MR-14)은 실행으로 확인하지 못했다.
- 이 점검 중 `pytest`를 `-p no:cacheprovider`로 돌려 작업 트리는 변하지 않았다(`git status --short` 비어 있음, 보고서 파일 제외).
