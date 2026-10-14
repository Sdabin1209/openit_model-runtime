# STATUS

갱신: 2026-10-15 (5차)

## 한 일
- 5차: I9 `load`를 역할별 설정·기대 해시로 변경(D45), 0.3.0. 역할별 검증·시험(역할 하나만 어긋난 해시, 역할 집합 불일치, 한 백엔드 공유와 역할별 디코딩). 67개 시험 통과. push는 헤드 확인 대기(v0.3.0 태그는 헤드가 붙인다).
- 4차: 헤드 답변 반영. 마지막 상수 문구를 `ProtectionPolicy`(`protect-1`)로 옮기고 판단 J1~J3을 정책에 기록, design.md에 "잠정" 표시. 61개 시험 통과.
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
- **사용자(도메인 소유자) 확정 대기**: 대체 문장의 마지막 상수 문구("죄송합니다. 다시 한 번 말씀해 주세요.", 잠정). 헤드가 사용자에게 올린다. 확정되면 새 정책 버전(`protect-2`)으로 바꾼다.
- (닫힘) 변경 요청 001: 헤드가 해석을 승인했다. 조립 순서·형식은 잠정 승인.
- vLLM 백엔드 구현과 `design.md` §11 GPU 시험(GPU 서버 필요). 그 전까지 `load`에 백엔드가 없으면 `BackendNotAvailable`.
- 실제 백엔드로 적합성 표 재실행(§11-10).
- 패키지를 Git 태그로 설치하는 경로 확인(이 리포지토리 태그는 아직 없음).

## 막힌 것
- GPU 없음.

## 알려야 할 것
- `serving_config` 형식이 바뀌었다(`protection_policy_version` 필수). model-lab이 매니페스트를 쓰기 전에 `validate_serving_config`를 부르면 알 수 있다.
