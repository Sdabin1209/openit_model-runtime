# STATUS

갱신: 2026-10-15 (3차)

## 한 일
- 개발 환경: `.venv`(pytest 9, jsonschema 4.26)에 설치. `pip install -e ../boundary-contracts`가 **된다**(import 확인). `-e .`도 된다.
- 헤드의 I9 개정(2bc3873)과 D36 출력 보호를 구현했다. 헤드 메시지로 전달받은 규칙 원문은 `docs/rules.md`에 옮겼다(D36 원문이 있는 `head/` 문서는 읽을 수 없어 메시지와 INTERFACES I9만 근거로 삼았다).
  - 파서 보호 P1~P6, 생성기 규칙 1~5(G1·G4는 기계 확인, G2·G3은 프롬프트로만 지시, G5 대체 문장).
  - `parse_raw`/`generate_raw`, `last_call_info`(outcome, protections), `ModelOutputInvalid` 삭제.
  - `schema_dir` 단일화(필수 키워드 인자, 항상 schemas 디렉터리), `configure` 삭제.
  - `serving_config.protection_policy_version` 추가, 프롬프트 `prompt-2`로 교체.
- 시험 58개 통과: `.venv/bin/python -m pytest`. 파서 P1~P6와 생성기 규칙 1~5를 규칙 문장 단위로 한 시험씩 썼고, 적합성 표(`load` 세션 대 `FakeModelSession`)와 예시·골든 프롬프트를 갱신했다.
- 문서: design.md(§2.5, §12), boundary.md, runbook.md, prompt-review.md(prompt-2 점검), CHANGELOG.md, rules.md, `change-requests/001`.
- 작업 단위별로 커밋했다(작성자 Sdabin1209, push 안 함).

## 남은 일
- **헤드 확인 대기**: `change-requests/001-output-protection-interpretation.md` — 대체 문장 조립 순서·형식, G1·G4의 기계 확인 범위, 파서 항목 단위 처리(현재는 전체 빈 목록). 답에 따라 `fallback.py`·`protection.py`와 시험을 고친다.
- vLLM 백엔드 구현과 `design.md` §11 GPU 시험(GPU 서버 필요). 그 전까지 `load`에 백엔드가 없으면 `BackendNotAvailable`.
- 실제 백엔드로 적합성 표 재실행(§11-10).
- 패키지를 Git 태그로 설치하는 경로 확인(이 리포지토리 태그는 아직 없음).

## 막힌 것
- GPU 없음. 변경 요청 001에 대한 헤드 답변.

## 알려야 할 것
- `docs/brief.md` 31행이 아직 `ModelOutputInvalid`를 말한다(헤드 소유라 고치지 않았다).
- `serving_config` 형식이 바뀌었다(`protection_policy_version` 필수). model-lab이 매니페스트를 쓰기 전에 `validate_serving_config`를 부르면 알 수 있다.
