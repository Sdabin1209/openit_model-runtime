# ADR 0026: setup-env.sh가 SetupEnvReport 한 줄로 시험 환경을 선언한다

## 상태
채택(2026-10-30, 헤드 지시 U43, 계약 boundary-contracts v2.6.0 `SetupEnvReport`).

## 맥락
deploy가 이웃 영역의 `.venv/bin/python`·`node_modules` 경로와 시험 도구(pytest/npm)를 짐작했다(`doctor.sh`, `check_all.py`). 구조 규칙 1(영역은 서로의 구조를 모른다) 위반이다(OPEN_GAPS U43, deploy CR-D30).

## 결정
1. `scripts/setup-env.sh`의 표준 출력은 **결과 JSON 한 줄뿐**이다: `{area, status, exit_code, seconds, environment{kind, path, test_command}}`. pip를 포함한 진행 기록은 모두 표준 오류로 보낸다.
2. 이 영역의 선언: `kind=python-venv`, `path=.venv`(리포 루트 기준 상대), `test_command=[".venv/bin/python","-m","pytest","-q"]`(리포 루트에서, 셸 없이, 영역 시험 전체).
3. 실패해도(`trap EXIT`) 마지막 줄은 `status=fail`과 종료 코드를 담은 보고다. 준비되지 못한 환경은 `environment`를 선언하지 않는다.
4. 멱등·인자 없음·비밀 없음은 그대로다. 계약 태그는 `Dockerfile`의 `ARG BOUNDARY_VERSION` 한 곳에서 읽는다(v2.6.0).

## 왜
계약 v2.6.0 `SetupEnvReport`(영역 -> deploy)가 정한 모양이다. deploy는 영역이 낸 선언만 읽는다(구조 규칙 1·6). `seconds`는 bash 내장 `SECONDS`다.

## 검토한 대안과 버리는 이유
- 환경을 별도 파일에 선언하고 deploy가 읽기: 영역 파일 구조를 deploy가 알게 된다. 기각.
- 실패 시 보고 없이 종료 코드만: deploy가 요약표를 채울 때 영역 이름·소요 시간을 따로 알아야 한다. 기각.

## 결과
얻는 것: deploy가 `.venv` 경로와 pytest를 짐작하지 않는다. 감수하는 것: 표준 출력에 다른 것을 쓰면 안 된다(시험이 한 줄만 나오는지 본다). 새 머신에서 계약 태그를 받지 못하면(네트워크·GitHub) `status=fail`이다.

## 되돌리는 조건
계약 3.0.0에서 `environment`가 필수가 되어도 이 영역은 이미 선언한다. 보고 모양이 바뀌면 따른다.
