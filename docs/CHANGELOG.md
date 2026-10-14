# 변경 이력

## 0.2.0 (미배포) — 2026-10-15
- 출력 보호(D36): `parse`/`generate`는 재시도 뒤에도 유효하지 않으면 예외 대신 규칙이 정한 대체 출력을 낸다. 파서 P1~P6, 생성기 G1·G4·G5(`docs/rules.md`). **`ModelOutputInvalid` 삭제.**
- 생성기 모델은 `response_text`만 낸다. `is_fallback`은 코드가 채운다.
- `parse_raw`/`generate_raw`(`RawResult`), `last_call_info`에 `outcome`·`protections`.
- `schema_dir`는 `load`·`render_prompt`의 필수 키워드 인자이고 항상 `.../schema/schemas`. `configure`·`SchemaDirRequired` 삭제.
- `serving_config`에 필수 키 `protection_policy_version`(`protect-1`) 추가. **이전 0.1.0의 `serving_config` 해시와 호환되지 않는다**(미배포).
- 프롬프트 `prompt-1` 삭제, `prompt-2` 추가(생성기 규칙 1~4, 파서 어휘 목록).
- 개발 환경: `.venv`, pytest.
- 보호 정책을 `ProtectionPolicy`로 분리(`protect-1`): 재료가 없을 때의 대체 문구(잠정)와 판단 J1~J3을 정책 버전에 묶음. `fallback_text(..., policy_version=)`.

## 0.1.0 (미배포)
- I9 호출 경로: `load`, `parse`/`generate`, `render_prompt`, `serving_config_hash`, `validate_serving_config`.
- 세 겹 출력 보장(제약 디코딩 백엔드, 항상 거치는 사후 검증, `max_attempts` 재시도, 시드 = 기본 시드 + 시도 번호).
- `FakeModelSession`/`FakeBackend`.
- 해시는 `boundary_contracts.canonical` 사용(자체 JCS 삭제).
