# 변경 이력

## 0.1.0 (미배포)
- I9 호출 경로: `load`, `parse`/`generate`, `render_prompt`, `serving_config_hash`, `validate_serving_config`.
- 세 겹 출력 보장(제약 디코딩 백엔드, 항상 거치는 사후 검증, `max_attempts` 재시도, 시드 = 기본 시드 + 시도 번호).
- `FakeModelSession`/`FakeBackend`.
- 해시는 `boundary_contracts.canonical` 사용(자체 JCS 삭제). 스키마는 호출자가 `schema_dir`로 넘김, 버전 불일치는 `ContractVersionMismatch`.
- 시험: 단위, 예시 입출력, 프롬프트 골든, 적합성 표(`load` 대 `FakeModelSession`).
- 실제 백엔드(vLLM)는 없음.
