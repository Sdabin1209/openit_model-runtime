# model-runtime 설계

상태: 초안. 호출 경로(Fake 백엔드까지)는 구현됐고 실제 백엔드는 없다(`docs/STATUS.md`). 표시 규칙: **[확인]** 계약·브리프에서 읽은 것, **[결정]** 사용자가 정한 것, **[제안]** 이 문서의 제안, **[미정]** 정해지지 않은 것.

## 1. 범위와 불변 조건

- 학습 후 평가, 게이트의 승인 평가, 서비스 추론이 같은 `render_prompt`, 같은 디코딩 경로, 같은 검증 경로를 쓴다. [확인: 브리프 §1, §4]
- `parse`/`generate`는 **항상 출력 계약의 스키마를 통과하는 값**을 돌려준다. 모델 출력이 재시도 뒤에도 유효하지 않으면 예외를 던지지 않고 규칙이 정한 대체 출력을 낸다(출력 보호, D36, §2.5, `docs/rules.md`). 보호는 조용히 지나가지 않는다: `last_call_info`에 종류와 건수를 남기고, `parse_raw`/`generate_raw`로 보호 전 품질을 잴 수 있다. [확인: I9]
- 소비하는 계약은 문진 스키마(`CONTRACT_VERSION` = 1)뿐이다. 해시 계산만 `boundary_contracts.canonical`(헤드 소유 기준 구현)을 가져다 쓴다(§10). `SUPPORTED_RUNTIME_CONTRACT_VERSION`은 이 정수 한 값이다. [확인]
- 모델이 판단하게 만드는 프롬프트를 두지 않는다. 파서는 발화를 항목으로 바꾸기만 하고, 생성기는 응답 지시(`Directive`)를 문장으로 바꾸기만 한다. [확인: 브리프 §6]
- 입력 발화를 로그에 남기지 않는다. 오류 메시지와 예외에도 발화 원문과 모델 출력 원문을 넣지 않는다(위반 위치와 길이만). [확인: 브리프 §5]
- 생성자는 환경 변수를 읽지 않는다. 설정은 인자(`serving_config`)로만 받는다. [확인: CONVENTIONS §7]

## 2. 출력 형식 보장 방식 (O14) [결정]

세 겹으로 보장한다. 어느 겹도 다른 겹을 생략하게 하지 않는다.

1. **제약 디코딩**: 교체 가능한 백엔드가 출력 스키마로 생성을 제약한다.
2. **사후 검증**: 제약 디코딩 사용 여부와 상관없이 **항상** 원래(완화하지 않은) 출력 스키마로 검증한다.
3. **재시도**: 검증 실패 시 작은 상한(`max_attempts`) 안에서 다시 생성한다.
4. **출력 보호**: 상한을 다 쓴 뒤에도 유효하지 않으면 규칙이 정한 대체 출력을 낸다(§2.5). 예외로 끝나지 않는다.

### 2.1 제약 디코딩 백엔드

- `ConstraintBackend` Protocol로 분리한다. 입력은 (제약용 스키마, 프롬프트, 디코딩 파라미터)이고 출력은 문자열이다. 실제 추론 엔진은 이 Protocol 구현 하나다. [제안]
- 엔진 선택은 model-runtime이 정한다. [확인: INTERFACES I9, O14] 엔진 선택은 백엔드 구현에만 영향을 주고 검증·재시도에는 영향을 주지 않는다. 백엔드 이름·버전은 `serving_config`에 기록되므로 해시에는 반영된다.
- **첫 백엔드: vLLM(프로세스 안 엔진) + 구조화 출력(JSON 스키마 제약, 문법 엔진은 xgrammar) + LoRA 어댑터 요청.** [제안]
  - 근거: (1) `load`가 어댑터 **디렉터리 경로**를 받으므로 경로로 LoRA를 붙이는 엔진이어야 하고, 한 베이스 모델에 LORA_A·LORA_B 두 어댑터를 올려 쓰는 구조가 맞다. (2) JSON 스키마 제약과 요청별 시드를 지원한다. 시드 규칙(기본 시드 + 시도 번호)에 필요하다. (3) 서비스의 추론 서버(survey-service 소유)와 같은 계열을 쓰면 학습 후 평가와 서비스가 같은 디코딩 경로를 쓰기 쉽다.
  - **확인하지 못한 것**: 이 환경에서 엔진 버전별 지원 기능(`oneOf`, `if/then`, `uniqueItems` 등)과 LoRA+구조화 출력 동시 사용을 확인하지 않았다. 위 근거는 사전 지식이며, 구현 첫 단계에서 GPU 환경으로 확인하는 스파이크를 하고 `supported_features`를 그 결과로 채운다. 맞지 않으면 백엔드만 교체한다.
  - 개발·CI에서는 GPU가 없으므로 `FakeBackend`를 쓰고, vLLM 의존은 선택 설치(extra)로 둔다. [제안]
- 백엔드는 자기가 지원하는 스키마 기능 집합을 선언한다(`supported_features`). [제안]

### 2.2 완화한 스키마

- 엔진이 지원하지 않는 스키마 기능(예: `oneOf`로 쓴 열거, `allOf`+`if/then`, `not`, `uniqueItems`, `minLength` 같은 키워드)이 있으면, 출력 스키마를 **완화**해 제약용 스키마를 만든다. 완화는 허용 범위를 넓히는 방향으로만 한다(제약이 올바른 출력을 막지 않게 하기 위해). [결정]
- 완화는 순수 함수 `relax(schema, supported_features) -> schema`이고, 무엇을 뺐는지 목록을 돌려준다. 같은 입력이면 같은 결과다. [제안]
- 완화로 빠진 규칙은 사후 검증이 보완한다. 예: `ParserOutput.Item`의 `intent == ANSWERED_INTENT`이면 `target`·`value`가 null이 아니어야 한다는 `allOf` 규칙. [확인: 스키마에 있는 규칙] 이 규칙을 제약이 지원하지 못하면 검증 단계에서 걸러지고 재시도 대상이 된다.
- 사후 검증은 원래 스키마를 쓴다. 검증용 스키마는 문진 스키마 파일 그대로이며 이 리포지토리에 복사해 고치지 않는다. [제안: 읽기 전용 원칙]
- 어떤 키워드가 완화됐는지는 `serving_config`에 영향을 주는 사실이다. 백엔드 이름과 `supported_features`가 `serving_config`에 기록되므로 해시에 반영된다(§4).

### 2.3 재시도

- 상한은 `serving_config.max_attempts`(총 시도 횟수, 첫 시도 포함)이다. 기본값 3은 헤드가 승인했다. [확인] 최댓값은, 이 리포지토리가 작게 제한한다(제안: 5, 구현하며 확정).
- **시드 규칙 [확인: I9, 헤드 승인]**: 시도 번호 k(0부터)의 시드는 `기본 시드 + k`다. 기본 시드는 `serving_config`에 기록된다. 온도 0에서 같은 프롬프트·같은 시드를 되풀이하면 결과가 같아 재시도가 무의미하므로, 시드만 바꾼다. 시도 간에 달라지는 값은 시드뿐이다. 시간·난수는 외부에서 읽지 않는다.
- **재시도 프롬프트 [확인: I9]**: 첫 시도와 같다. 검증 오류를 덧붙이지 않는다.
- 온도 0에서는 시드가 결과를 바꾸지 못할 수 있다(엔진에 따라 탐욕 디코딩은 시드와 무관). 이 경우 재시도가 같은 출력을 낼 수 있음을 설계가 인정한다. 재시도가 실제로 효과가 있는 설정인지는 `validate_serving_config`가 막지 않고(온도 0도 허용), 평가에서 `attempts` 분포로 확인한다. [제안]
- 상한을 다 쓰면 마지막 시도의 출력에 출력 보호를 적용한다(§2.5). 유효 여부 판단에는 스키마 위반뿐 아니라 보호 규칙에 해당하는 것(파서 P2~P6, 생성기 G1·G4)도 들어간다. 규칙 위반도 재시도 대상이다. [해석: docs/rules.md]
- 재시도 횟수는 호출 결과 메타데이터(`attempts`)로 호출자가 볼 수 있어야 한다. 반환 타입은 출력 계약 그대로여야 하므로, 메타데이터는 `ModelSession`의 별도 조회 경로로 제공한다(§3). [제안]

### 2.5 출력 보호 (D36) [확인: I9, 규칙은 `docs/rules.md`]

- **파서**: 형식을 못 지키면 빈 목록, 항목이 규칙을 어기면(의도·진행 조작·안내 ID·대상·값) 규칙이 정한 대로 고친다(P1~P6). 의도를 바꿀 때는 대상·값·진행 조작·안내 ID를 비운다. 대상이 문항 정의에 없는 항목은 고치지 않는다.
- **생성기**: 모델은 `response_text`만 낸다. 출력 스키마에서 `is_fallback`을 뺀 "모델 출력 스키마"로 제약·검증하고, `is_fallback`은 코드가 채운다(정상 false). 형식 위반이나 기계로 확인되는 규칙 위반(G1 응답 지시에 없는 숫자, G4 위험 안내 문장 누락)이면 문장 재료를 그대로 읽는 대체 문장(`fallback_text`, 발화를 쓰지 않는 순수 함수)을 내고 `is_fallback = true`. 규칙 2·3은 기계로 확인하지 못하고 프롬프트로만 지시한다(보호 전 품질은 `generate_raw` 평가로 본다).
- **보호는 조용히 지나가지 않는다**: `last_call_info.outcome`(ok·repaired·fallback·raw)과 `protections`(이름, 건수). 원문은 담지 않는다.
- **`*_raw`**: 재시도 루프는 같고 보호만 적용하지 않는다. `RawResult(output, valid, errors)`. output은 마지막 시도의 JSON으로 읽힌 모델 출력(읽지 못하면 None), errors는 원문 없는 이름(`json`, `schema:<경로>:<키워드>`, `rule:<이름>`).
- **보호 정책 버전**: 출력을 바꾸는 값과 해석은 `ProtectionPolicy`(`protection.py`의 `PROTECTION_POLICIES`)에 묶는다. 바꾸려면 새 버전(`protect-2`…)을 만들고 `serving_config.protection_policy_version`에 기록해 해시에 반영한다. `protect-1`의 내용:
  - **대체 문장의 마지막 상수**(재료가 하나도 없을 때): "죄송합니다. 다시 한 번 말씀해 주세요." **[잠정]** 규칙에 없는 문구이며 확정은 도메인 소유자 몫이다(헤드가 사용자에게 올린다). 확정되면 새 정책 버전으로 바꾼다.
  - **판단 J1**(파서): 고친 뒤에도 항목 하나가 스키마를 통과하지 못하거나 항목이 객체가 아니면 전체를 빈 목록(P1)으로 한다. 보수적이고 `item_no` 정렬이 깨지는 것을 막는다. [헤드 승인]
  - **판단 J2**(P4): `guide_id`가 null이 아니고 `guide_list`에 없으면 의도와 무관하게 OUT_OF_SCOPE. [헤드 승인]
  - **판단 J3**(P5): '문항 전체에 값을 붙임'은 `whole_question`이 true이고 `value`가 null이 아닌 경우. [헤드 승인]
  - 대체 문장 조립(알릴 것 → 말할 값 → 행동, `"{이름}: {값}"`, 선택지는 쉼표) [헤드 잠정 승인]. 고정 문장 행동은 고정 문장만 읽고 대상의 질문 문장은 덧붙이지 않는다. 규칙 4는 `RISK_NOTICE` 문장의 verbatim 포함을 요구하고 바꿔 말하기를 허용하지 않는다(안전한 쪽). 규칙 2의 금지 표현 목록은 만들지 않는다. 규칙 2·3은 `generate_raw` 평가(model-lab)로 본다.
- 해석이 모호한 부분(문장 재료 조립 순서, G1·G4의 기계 확인 범위)은 `change-requests/001-output-protection-interpretation.md`에서 확인을 요청한다.

### 2.4 예외

- 라이브러리 예외는 인프라·설정 문제에만 쓴다: `ServingConfigMismatch`, `UnsupportedServingConfig`, `ContractVersionMismatch`, `BackendNotAvailable`, 입력·설정 형식 위반의 `ContractViolation`. 모두 `BoundaryError`가 아니다. 출력 문제에는 예외를 쓰지 않는다(`ModelOutputInvalid` 삭제). [확인: I9, D36]
- 입력이 입력 계약을 위반하면 모델을 부르기 전에 거부한다. 예외는 `ContractViolation`(I8과 같은 이름)을 쓴다. [제안]
- 로딩 거부 예외는 §4.3.

## 3. 컴포넌트 [제안]

| 컴포넌트 | 책임 |
| --- | --- |
| `prompts` | 역할별(`LORA_A`, `LORA_B`) 프롬프트 템플릿과 `render_prompt(role, input, *, serving_config)`. 순수 함수. 지원하는 템플릿 버전 집합을 가진다. |
| `schema_guard` | 문진 스키마 로딩, 입력 검증, 출력 검증, `relax`. `CONTRACT_VERSION` 일치 확인. |
| `serving_config` | `ServingConfig` 형식 정의, `validate_serving_config`, `serving_config_hash`. |
| `backends` | `ConstraintBackend` Protocol과 구현. |
| `session` | `ModelSession` 구현: 렌더 → 생성 → 검증 → 재시도 루프. |
| `fake` | `FakeModelSession`. `session`과 **같은 루프**를 쓰고 백엔드만 가짜로 바꾼다(§5). |
| `protection` | 파서 대체 출력 규칙(P1~P6)과 생성기 규칙 확인(G1·G4). 순수 함수. |
| `fallback` | 생성기 대체 문장 조립. 순수 함수. |
| `errors` | `ContractViolation`, `ServingConfigMismatch`, `UnsupportedServingConfig`, `ContractVersionMismatch`, `BackendNotAvailable`. |

`ModelSession`은 Protocol로 정의하고 `parse`, `generate`, `parse_raw`, `generate_raw` 외에 `serving_config_hashes`(역할별)와 마지막 호출의 메타데이터(`last_call_info`: 시도 횟수, 완화된 키워드, 보호 결과)를 노출한다. 발화·출력 원문은 메타데이터에 넣지 않는다. [제안]

## 4. ServingConfig와 serving_config_hash [확인: I9, 헤드 결정]

`ModelPackageManifest.serving_config`의 형식은 model-runtime이 정하고 `serving_config_hash`로 고정한다. 어댑터에는 버전 정보를 넣지 않는다. 어댑터는 `adapter_hash`, 설정은 `serving_config_hash`로 고정된다.

### 4.1 필수 내용

I9가 반드시 포함하라고 한 것: 프롬프트 템플릿 버전, 디코딩 설정(제약 모드, 온도, 기본 시드), `max_attempts`.

키 구조 [제안, 구현하며 확정]:

```
prompt_template_version: str          # 역할 공통 또는 역할별(구현에서 결정)
decoding:
  constraint_mode: "constrained" | ... # 제약 모드. 값 집합은 구현에서 확정
  temperature: number
  seed: int                            # 기본 시드. 시도 k의 시드 = seed + k
  (top_p, max_tokens 등 샘플링 파라미터)
max_attempts: int
protection_policy_version: str        # 출력 보호 규칙 버전(현재 protect-1)
constraint:                            # 백엔드 이름·버전, supported_features, 완화 정책 버전
output_contract: int                   # 문진 스키마 정수 버전(CONTRACT_VERSION)
```

디코딩 방식과 설정이 모두 여기에 들어가므로, 평가와 서비스가 같은 `serving_config`를 받으면 같은 경로를 쓴다.

### 4.2 함수

- `validate_serving_config(cfg) -> None`: 형식 위반이면 `ContractViolation`. model-lab이 매니페스트를 쓰기 전에 부른다. 알 수 없는 키는 거부한다(이 형식은 이 리포지토리가 정하므로 엄격하게). [제안]
- `serving_config_hash(cfg) -> str`: 먼저 형식을 검증하고, 정규화한 설정을 RFC 8785(JCS)로 직렬화한 바이트의 SHA-256에 `sha256:`를 붙인다. [확인: CONVENTIONS §2] 받은 그대로 해시하고 기본값을 채우지 않는다(구현됨). 필수 키만 있으므로 기본값 채우기가 필요 없고, 매니페스트에 기록된 값과 해시 대상이 일치한다.

### 4.3 `load`의 역할별 검증 [확인: I9, D45]

`load(adapters, serving_configs, *, schema_dir, expected_serving_config_hashes, backend=None)`. `ReleaseManifest.adapters[role]`과 모델 패키지가 역할별이므로 설정과 기대 해시도 역할마다 받는다.

1. 어댑터·설정·기대 해시의 역할 집합이 서로 다르거나 {LORA_A, LORA_B}가 아니면 `ContractViolation`(어댑터 디렉터리가 없을 때도).
2. `schema_dir` 상위의 `CONTRACT_VERSION`이 다르면 `ContractVersionMismatch`.
3. 역할마다(LORA_A, LORA_B 순서) 형식(`ContractViolation`) → 지원 여부(`UnsupportedServingConfig`, 메시지에 역할) → 해시(`ServingConfigMismatch`, `.role`에 역할) 순서로 확인한다. 한 역할이 어긋나면 나머지 역할이 맞아도 로딩을 거부한다. 둘 다 위반일 때 어느 예외가 먼저 나는지는 약속하지 않는다.

**역할별로 적용되는 것**: 프롬프트 템플릿 버전, 디코딩 설정(온도·시드·제약 모드·`max_tokens`), `max_attempts`, 보호 정책 버전. 파서(LORA_A)는 결정적이고 생성기(LORA_B)는 다를 수 있다. 세션의 `serving_config_hashes`는 역할별 해시다.

**기반 모델 공유 [제안]**: 세션은 백엔드 객체 하나를 두 역할이 함께 쓴다. 요청에 `role`이 들어 있으므로 실제 백엔드는 기반 모델 가중치를 한 번만 올리고 요청마다 역할의 어댑터를 고르면 된다(VRAM 절약). 역할별 설정이 달라도 `constraint`(백엔드 이름·버전·`supported_features`)는 사용하는 백엔드와 모든 역할에서 같아야 하고, 아니면 `UnsupportedServingConfig`다. 기반 모델이 서로 다른 두 어댑터를 지원할지는 [미정]이다(백엔드 구현 때 정한다. 첫 백엔드 시험 §11-2에 항목이 있다).

## 5. FakeModelSession [결정]

- 실제 `ModelSession`과 같은 클래스 경로(렌더 → 백엔드 → 사후 검증 → 재시도 → 출력 보호)를 쓴다. 가짜인 것은 `ConstraintBackend` 하나다(`FakeBackend`). [결정]
- `FakeBackend`는 시험용 스크립트(시도별로 돌려줄 문자열 목록, 또는 입력에서 결정적으로 유도한 출력)를 받는다. 위반 출력을 시도 N번째에 내보내게 해서 재시도·상한 초과 경로를 GPU 없이 시험한다.
- `FakeModelSession`도 `serving_configs`와 `expected_serving_config_hashes`(역할별)를 받고 로딩 거부 규칙(§4.3)을 같은 코드로 적용한다.
- 적합성 테스트: 실제 `ModelSession`(스크립트된 백엔드를 붙인 것)과 `FakeModelSession`이 같은 시나리오 표를 통과해야 한다. [제안: CONVENTIONS §7의 적합성 테스트 요구에 따름]

## 6. 프롬프트 [제안]

- `render_prompt(role, input, *, serving_config) -> str`: 입력을 해당 역할의 입력 계약으로 검증하고, `serving_config`의 `prompt_template_version`에 해당하는 템플릿에 채운다. 지원하지 않는 버전이면 `UnsupportedServingConfig`. 학습 데이터 변환(model-lab)과 서빙이 같은 함수를 부른다.
- 템플릿은 정규화된 입력(JCS 직렬화 등 결정적 직렬화)을 사용해 같은 입력이 항상 같은 문자열이 되게 한다. 입력 필드 순서·공백에 의존하지 않는다.
- 파서 프롬프트에는 판단 지시(저장 여부, 정정 여부의 최종 판정 등)를 넣지 않는다. 의도 분류(`Intent`)는 스키마가 모델 출력으로 정한 것이므로 허용되나, 구체적으로 어디까지가 "해석"이고 어디부터가 "판단"인지의 경계는 turn-engine 역할과 겹치므로 프롬프트 작성 때 점검 목록으로 둔다. [미정: 점검 목록은 구현 시 작성]
- 템플릿 버전은 `serving_config.prompt_template_version`으로 기록된다.

## 7. 관측과 개인정보 [제안]

- 로그에는 발화, 응답 문장, 모델 출력 원문을 남기지 않는다. 시도 횟수, 위반 종류, 지연 시간, 해시만 남길 수 있다.
- 로깅은 표준 `logging`의 표준 레코드만 쓰며, 설정은 호출자 몫이다.

## 8. 테스트 계획 [제안]

- 단위: `relax`(허용 범위가 줄지 않는 속성 테스트), `serving_config` 정규화·해시 안정성, `render_prompt` 결정성.
- 경로: 첫 시도 성공, 재시도 뒤 성공, 상한 뒤 보호(파서 항목 고침·빈 목록, 생성기 대체 문장), 완화 때문에 제약이 놓친 위반(`ANSWERED_INTENT`인데 `value`가 null)을 사후 검증이 잡음.
- 보호 규칙: 파서 P1~P6와 생성기 규칙 1~5를 규칙 문장 단위로 한 시험씩(`tests/test_protection.py`). 규칙 2·3은 기계 확인이 없어 프롬프트 포함 여부와 대체 문장 동작으로 시험한다.
- 개인정보: 예외와 로그에 발화·출력 원문이 없음을 확인하는 테스트.
- 적합성: 실제 세션(스크립트된 백엔드)과 `FakeModelSession`의 동일 시나리오 통과.
- 입출력 예시: `tests/examples/`에 입력 계약 예시와 기대 출력 예시를 둔다. 예시는 문진 스키마로 검증한다.

## 9. 열린 항목

| 항목 | 상태 |
| --- | --- |
| 첫 백엔드(vLLM)의 `supported_features`와 LoRA+구조화 출력 동시 사용 | 헤드가 잠정 채택. §11 체크리스트로 GPU 시험 뒤 확정 |
| `ServingConfig` 키 이름과 `constraint_mode` 값 집합 | 구현하며 확정 |
| `max_attempts` 최댓값 | 제안 5 |
| 온도 0에서 시드 변경이 출력을 바꾸지 않는 엔진 | 인정하고 `attempts` 분포로 관찰(§2.3) |
| 스키마 사본 묶음 | 닫힘: 묶지 않고 호출자가 `schema_dir`로 넘긴다 |
| 지연 수치 요구 | survey-service와 협의 필요(브리프 §5) |

닫힌 항목: 어댑터 내 버전 기록(하지 않음), `load`의 기대 해시(필수 키워드 인자), 예외 계층(`BoundaryError` 아님), 재시도 프롬프트·시드 규칙. 모두 헤드가 INTERFACES I9에 반영했다.

## 10. 구현에서 정한 것 (2026-10-15 기준) [확인: 테스트로 확인한 것은 `tests/test_runtime.py`]

- 구현은 `src/model_runtime/`. 의존은 `jsonschema`와 `boundary-contracts`. 개발은 `.venv`(pytest, jsonschema 4.26, `pip install -e ../boundary-contracts -e .`)에서 한다. 시험: `.venv/bin/python -m pytest`.
- `load`의 선택 인자 `backend`(시험·조립용, I9에 반영됨). `backend`가 없으면 `BackendNotAvailable`(`UnsupportedServingConfig`의 하위)을 던진다. vLLM 백엔드는 아직 없다.
- 스키마를 메모리로 읽을 때 `$id`를 뺀다. 문진 스키마의 상대 `$id`(`standalone/...`) 때문에 `#/$defs/..` 참조가 원격 주소로 풀리려 해서다. 파일은 고치지 않는다.
- **스키마 위치 [확인: I9 개정]**: 라이브러리에 묶지 않는다. 호출자가 `schema_dir`를 필수 키워드 인자로 넘긴다. 항상 `<계약명>.json`이 있는 `.../schema/schemas` 디렉터리이고 `CONTRACT_VERSION`은 그 상위에서 읽는다(서비스든 다른 영역이든 같다). `configure` 전역 설정은 없다. 버전이 다르거나 읽을 수 없으면 `ContractVersionMismatch`. 환경 변수는 읽지 않는다.
- **해시 [확인: HEAD 결정]**: 자체 JCS 구현을 지우고 `boundary_contracts.canonical.sha256_hash`를 쓴다(개발 중 `PYTHONPATH=../boundary-contracts`, 설치는 Git 태그). 실수 직렬화를 직접 하지 않는다. 브리프 §3의 "경계 계약에 의존하지 않는다"는 이 해시 라이브러리 의존으로 달라졌다. 공통 규칙은 boundary-contracts를 의존 대상으로 허용한다.
- 파서 출력의 의미 검사(예: `evidence` 오프셋이 발화 범위 안인지)는 스키마가 정하지 않아 하지 않는다.
- 출력 JSON의 `NaN`·`Infinity`는 거부한다.

## 11. 첫 백엔드(vLLM) GPU 시험 체크리스트 [제안]

GPU 서버가 필요하다. 이 항목을 확인하기 전에는 `BackendNotAvailable`로 둔다. 결과는 `supported_features`, 백엔드 버전, 완화 정책에 반영하고 이 절에 날짜와 함께 기록한다. 항목별로 ○/×와 사용한 버전을 적는다.

1. **환경**: GPU 모델, 드라이버, vLLM 버전, 문법 엔진(xgrammar 등) 버전, 베이스 모델 id·revision. `serving_config.constraint.backend_version`에 무엇을 적을지 정한다.
2. **LoRA 로딩**: 어댑터 **디렉터리 경로**로 LORA_A·LORA_B 두 개를 한 베이스 모델에 올릴 수 있는가. 요청마다 어댑터를 고를 수 있는가. 어댑터 순위(rank) 상한.
3. **LoRA + 구조화 출력 동시 사용**: 어댑터를 붙인 요청에 JSON 스키마 제약이 함께 적용되는가.
4. **스키마 기능 지원표**: `ParserOutput`, `GeneratorOutput` 스키마로 각각 시험해 어느 키워드가 받아들여지고, 무시되고, 오류인지 기록한다. 대상: `$ref`/`$defs`, `oneOf`(const 열거), `anyOf`, `enum`, `const`, `allOf`, `if/then`, `not`, `uniqueItems`, `minLength`, `minimum`, `additionalProperties:false`, `required`, `type` 배열.
5. **무시와 거부 구분**: 엔진이 키워드를 **조용히 무시**하는지 **오류로 거부**하는지. 조용히 무시하면 `supported_features`에 넣으면 안 되므로(완화되지 않은 채 제약이 느슨해진다) 이 구분이 핵심이다. 오류로 거부하는 키워드는 완화로 뺀다.
6. **시드 재현성**: 같은 프롬프트·시드·온도에서 결과가 같은가. 온도 0에서 시드가 결과를 바꾸는가(재시도 효과, §2.3). 온도 > 0에서 `시드 + 시도 번호`로 다른 출력이 나오는가.
7. **max_tokens 도달**: 제약 디코딩 중 `max_tokens`에서 잘리면 JSON이 닫히지 않는다. 이때 응답이 어떻게 오는지(잘림 표시 여부)와 사후 검증이 이를 위반으로 잡는지.
8. **지연**: 역할별 요청 지연(p50·p95), 첫 요청의 문법 컴파일 비용, 스키마 캐시 여부. 수치 요구는 survey-service와 정한다.
9. **결정성 점검**: 같은 서버를 두 번 띄워 같은 입력·시드 결과가 같은지. 평가와 서비스가 같은 서버 설정을 쓸 때 차이가 나는 요인(배치 크기, 텐서 병렬 등).
10. **적합성**: 같은 시나리오 표(`tests/test_examples_conformance.py`)를 실제 백엔드로 돌려 `FakeBackend`와 같은 예외·시도 횟수가 나오는지(출력 내용은 스크립트와 달라도 계약은 같아야 한다).
11. **개인정보**: 엔진·서버가 프롬프트·출력을 로그에 남기는 기본 설정이 있는지, 끄는 방법.

### 12. 변경 기록 (설계)
- 2026-10-15: 출력 보호(D36) 반영. `ModelOutputInvalid` 삭제, `*_raw`, `is_fallback`을 코드가 채움, `schema_dir` 단일화·`configure` 삭제, `protection_policy_version` 추가, 프롬프트 `prompt-1` 삭제 후 `prompt-2`(생성기 규칙 1~4 포함, 모델은 `response_text`만 출력). `prompt-1`은 어느 릴리스·모델 패키지에도 쓰인 적이 없다(0.x 미배포).
- 2026-10-15 (0.3.0): `load`를 역할별 설정·기대 해시로 변경(D45). `ModelSession.serving_config_hash` → `serving_config_hashes`(역할별), `ServingConfigMismatch.role`, 역할 집합 불일치는 `ContractViolation`. 소비자는 survey-service 하나(0.2.0 사용 중)였다.
