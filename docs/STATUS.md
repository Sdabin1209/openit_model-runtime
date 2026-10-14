# STATUS

갱신: 2026-10-15 (2차)

## 한 일
- 브리프·I9·CONVENTIONS·문진 스키마 읽음. `docs/design.md`, `boundary.md`, `runbook.md`, `prompt-review.md`, `CHANGELOG.md` 작성.
- `src/model_runtime/` 구현: 예외, ServingConfig 검증·해시(`boundary_contracts.canonical` 사용), 스키마 로딩(`schema_dir`)·완화(`relax`), 프롬프트 `prompt-1`, 백엔드 Protocol과 `FakeBackend`, 세션 루프(검증·재시도·시드=기본+시도), `load`, `FakeModelSession`.
- 헤드 결정 반영: 자체 JCS 삭제, 스키마 사본 미포함(호출자가 `schema_dir`), `ContractVersionMismatch`.
- 시험 26개 통과: 단위, 예시 입출력(`tests/examples/`), 프롬프트 골든, 적합성 표(`load` 세션과 `FakeModelSession`이 같은 시나리오 10개와 로딩 거부를 같은 결과로 통과).
  실행: `PYTHONPATH=src:tests:../boundary-contracts python3 -W error::DeprecationWarning -m unittest discover -s tests`
- vLLM GPU 시험 체크리스트: `design.md` §11.
- 역할 분리 점검 목록: `docs/prompt-review.md`.

## 남은 일 (GPU·pip 필요)
- vLLM 백엔드 구현과 §11 시험(GPU 서버, 사용자가 켜 줄 수 있음). 그 전까지 `load`에 백엔드가 없으면 `BackendNotAvailable`.
- pip 설치 뒤: pytest 전환 여부 결정, 패키지 설치 시험(`pip install` with boundary-contracts Git 태그), 컨테이너는 해당 없음(라이브러리).
- 실제 백엔드로 적합성 표 재실행(§11-10).

## 확인이 필요한 것
- `prompt-1`은 초안이다. 임상·turn-engine 담당의 검토를 받지 않았다. `GeneratorOutput.is_fallback`을 모델이 채우는지(프롬프트가 아직 이 필드를 다루지 않는다)를 확인해야 한다 — 스키마 소유 survey-service/헤드에 확인 필요. 변경 요청으로 쓸 수준인지는 아직 판단하지 않았다.
- 서비스의 `schema_dir` 표현: HEAD 메시지가 "자기 `schema/`"와 "`schema/schemas`"를 섞어 썼다. 둘 다 받게 구현했다. INTERFACES I9의 문구를 하나로 정리하면 좋다.

## 막힌 것
- pip 없음(사용자가 설치 예정), GPU 없음.

## 커밋
작업 트리의 변경을 커밋했다(HEAD 지시). push는 하지 않았다.
