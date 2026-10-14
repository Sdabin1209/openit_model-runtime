# model-runtime 경계

상태: 초안. 정확한 시그니처는 `../boundary-contracts/INTERFACES.md`의 I9가 정한다.

## 소유 저장소
없음(공유 라이브러리).

## 제공 (I9)
`load`(설정과 기대 해시는 역할별, D45), `ModelSession.parse/generate`, `render_prompt`, `serving_config_hash`, `validate_serving_config`, `FakeModelSession`(+`FakeBackend`, `fake_serving_config`), `RUNTIME_VERSION`, `SUPPORTED_RUNTIME_CONTRACT_VERSION`, `parse_raw`/`generate_raw`(`RawResult`), `last_call_info`. 예외는 인프라·설정·입력 문제에만 쓴다: `ServingConfigMismatch`·`UnsupportedServingConfig`·`ContractVersionMismatch`·`ContractViolation`·`BackendNotAvailable`(모두 `BoundaryError`가 아님). 출력 문제는 예외가 아니라 출력 보호(D36)로 처리한다.

`schema_dir`는 `load`와 `render_prompt`의 필수 키워드 인자이고 항상 `<계약명>.json`이 있는 `.../schema/schemas`다(CONTRACT_VERSION은 상위에서 읽음). 전역 설정 함수는 없다. `load`의 선택 인자 `backend`는 시험·조립용이다. `last_call_info`는 시도 횟수, 완화된 키워드, 보호 결과(종류와 건수)를 담고 원문은 담지 않는다.

## 소비
- 문진 스키마 정수 버전 1(`SUPPORTED_RUNTIME_CONTRACT_VERSION`). 다른 값이면 `ContractVersionMismatch`.
- `boundary_contracts.canonical`(해시 기준 구현, 헤드 소유). 그 밖의 SDK 의존 없음.

## 호출자가 지켜야 할 것
- 역할마다 `ModelPackageManifest.serving_config`와 `serving_config_hash`를 `serving_configs[role]`, `expected_serving_config_hashes[role]`로 넘긴다. 어댑터·설정·해시의 역할 집합은 {LORA_A, LORA_B}로 같아야 한다(아니면 `ContractViolation`). 한 역할만 어긋나도 로딩을 거부한다(`ServingConfigMismatch.role`).
- 역할별 설정이 달라도 `constraint`(백엔드 이름·버전·지원 기능)는 두 역할이 같아야 한다. 두 역할은 한 백엔드를 공유한다.
- `UnsupportedServingConfig`와 `ServingConfigMismatch` 중 어느 쪽이 먼저 나는지는 약속하지 않는다(둘 다 위반일 때).
- 같은 `serving_config`로 평가와 서비스를 호출해야 같은 디코딩 경로가 된다.
- 학습 데이터 변환은 `render_prompt(..., serving_config=...)`를 쓴다.

## 장애 영향
- 모델 출력이 깨져도 `parse`/`generate`는 예외 없이 규칙이 정한 대체 출력을 낸다(턴은 실패하지 않는다). 호출자는 `last_call_info`로 보호가 적용됐는지 본다(생성기는 `is_fallback`). 파서가 빈 목록을 내면 뒤의 컴포넌트가 "다시 말씀해 주세요"로 잇는다. 보호가 잦으면 모델 품질 문제이므로 model-lab이 `*_raw`로 잰다.
- 로딩 거부는 서비스 시작·릴리스 적재 실패로 이어진다. 이전 릴리스를 유지하는 책임은 서비스에 있다(I4).
- 개인정보: 로그와 예외에 발화·모델 출력 원문을 담지 않는다.
