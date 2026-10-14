# model-runtime 경계

상태: 초안. 정확한 시그니처는 `../boundary-contracts/INTERFACES.md`의 I9가 정한다.

## 소유 저장소
없음(공유 라이브러리).

## 제공 (I9)
`load`, `ModelSession.parse/generate`, `render_prompt`, `serving_config_hash`, `validate_serving_config`, `FakeModelSession`(+`FakeBackend`, `fake_serving_config`), `RUNTIME_VERSION`, `SUPPORTED_RUNTIME_CONTRACT_VERSION`, 예외 `ModelOutputInvalid`·`ServingConfigMismatch`·`UnsupportedServingConfig`·`ContractVersionMismatch`·`ContractViolation`(부가: `BackendNotAvailable`, `SchemaDirRequired`)(모두 `BoundaryError`가 아님).

`load`의 I9 밖 선택 인자(헤드 승인): `backend`, `schema_dir`. 문진 스키마는 호출자가 `schema_dir`로 넘긴다(`schemas/` 또는 그 상위 `schema/`). `render_prompt`처럼 인자가 없는 호출은 `configure(schema_dir=...)`를 먼저 부른다. `ModelSession.last_call_info`(시도 횟수, 완화된 키워드)는 I9 밖 추가 속성이며 원문을 담지 않는다.

## 소비
- 문진 스키마 정수 버전 1(`SUPPORTED_RUNTIME_CONTRACT_VERSION`). 다른 값이면 `ContractVersionMismatch`.
- `boundary_contracts.canonical`(해시 기준 구현, 헤드 소유). 그 밖의 SDK 의존 없음.

## 호출자가 지켜야 할 것
- `ModelPackageManifest.serving_config_hash`를 `expected_serving_config_hash`로 넘긴다.
- `UnsupportedServingConfig`와 `ServingConfigMismatch` 중 어느 쪽이 먼저 나는지는 약속하지 않는다(둘 다 위반일 때).
- 같은 `serving_config`로 평가와 서비스를 호출해야 같은 디코딩 경로가 된다.
- 학습 데이터 변환은 `render_prompt(..., serving_config=...)`를 쓴다.

## 장애 영향
- 출력이 `max_attempts`까지 스키마를 통과하지 못하면 `ModelOutputInvalid`. 호출자(turn 처리)가 대체 경로를 정한다. 이 라이브러리는 대체 응답을 만들지 않는다.
- 로딩 거부는 서비스 시작·릴리스 적재 실패로 이어진다. 이전 릴리스를 유지하는 책임은 서비스에 있다(I4).
- 개인정보: 로그와 예외에 발화·모델 출력 원문을 담지 않는다.
