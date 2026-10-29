# ADR 0027: 계약 3.0.0 전환 (ExtractRequest만, action_rules_version, 라벨 정직성)

## 상태
채택(2026-10-30, 헤드 지시 U300·U50, 계약 boundary-contracts v3.0.0). [ADR 0021](0021-server-guarantees-action-rules.md)·[0024](0024-server-applies-value-rules.md)·[0025](0025-build-entrypoint-and-contract-schemas.md)를 이어 받는다.

## 맥락
계약 3.0.0은 2.x 호환을 지우는 깨지는 변경이다: I9 extract 본문은 `ExtractRequest{input, flow, fields}`만, `turn_engine_version`은 `action_rules_version`으로, `BuildImageRequest.versions.turn_engine`과 이미지 라벨 `io.openit.turn-engine` 제거. 또 deploy 감사(U50)가 라벨이 요청 값이지 실제 설치 버전이 아닐 수 있음을 지적했다.

## 결정
1. **서버는 `ExtractRequest`만 받는다.** 옛 `ActionExtractionInput` 본문과 `fields`가 없는 요청은 모델을 부르지 않고 400 INVALID_RECORD다. flow·fields가 항상 있으므로 `ExtractRole.call(backend, input, flow, fields)`와 `ModelSession.extract(input, flow, fields)`에서 "없을 때" 분기를 지웠다. 원시 결과를 내는 `extract_raw`(평가용)는 스키마 통과 여부만 본다.
2. **계보 표지는 `action_rules_version`**이며 값은 계약 규칙 데이터의 `rules_version`을 `boundary_contracts.action_rules.load_rules()`로 읽는다(`model_runtime.ACTION_RULES_VERSION`). 리터럴로 적지 않는 이유: 이 서버가 보증하는 규칙의 정본이 그 데이터라 규칙이 바뀔 때 어긋나지 않게 한다. `make_neutral_package.py`·`prepare_bundle.py`는 이 값을 쓰고(`--action-rules-version`으로 덮어쓸 수 있다) 환경 변수 `MODEL_RUNTIME_TURN_ENGINE_MARKER`는 없앴다. 서버가 적재 때 이 표지를 비교해 거절하는 동작은 이번에 넣지 않았다(요청 밖이고, 비교는 survey-service·release-gate가 한다).
3. **라벨 정직성(U50).** `build-image.sh`는 라벨 값을 요청 문구가 아니라 실제 소스에서 얻고, 어긋나면 이미지를 만들기 전에 실패한다: 요청의 계약 버전 = `dependency_sources.boundary` 트리의 `pyproject` 버전 = 이 영역의 고정(`Dockerfile ARG BOUNDARY_VERSION`), 요청의 문진 스키마 주 버전 = 스키마 디렉터리의 `CONTRACT_VERSION`, 요청의 model-runtime 버전 = `source_dir`의 `pyproject` 버전. 빌드 뒤에는 이미지 안에서 `boundary-contracts`·`RUNTIME_VERSION`·`/opt/survey-schema/CONTRACT_VERSION`을 읽어 라벨과 대조하고 다르면 이미지를 지우고 실패한다.
4. 이미지 라벨 `io.openit.turn-engine`과 `ARG TURN_ENGINE_VERSION`을 지웠다.
5. GPU 세션 도구(`measure.py`, `gpu_serve_check.py`, `gpu_probe.py`)는 ExtractRequest로 보낸다. 값 규칙용 칸 규격(QuestionFieldSpec)의 정본 데이터는 이 영역이 읽을 수 있는 곳에 없어(문진 스키마 `data/`에는 `question_flow.json`뿐), 측정은 아무 칸도 판정하지 않는 자리표시 규격을 쓰고 결과 JSON의 `rules_context`에 그렇다고 남긴다. 흐름표는 키트에 문진 스키마 `data/`가 있으면 그것(`--flow-file`)을 쓴다. 그러므로 **측정은 값 규칙을 검사하지 않는다**.

## 왜
계약 3.0.0 CHANGELOG(1·2항), U50, 구조 규칙 1·R7(서버가 항상 보증하므로 받는 쪽이 다시 검사하지 않고, 보증을 건너뛰는 길을 두지 않는다). 거짓 라벨은 deploy가 버전 일치를 라벨로 검사하므로 일치 검사 전체를 무의미하게 만든다.

## 검토한 대안과 버리는 이유
- 옛 본문을 한 판 더 받기: 3.0.0이 지우기로 한 호환이다. 기각.
- 라벨을 요청에서 그대로 찍고 사후 검사만: 사후 검사가 없으면 거짓이 남는다. 사전(소스)과 사후(이미지) 둘 다 본다.
- 측정에 가짜 칸 규격을 정교하게 만들어 넣기: 근거 없는 규격이 값 규칙 결과를 흐린다. 비활성임을 밝히는 쪽을 골랐다.

## 결과
얻는 것: 규칙 보증을 우회하는 길이 없고 라벨이 실제 설치 버전과 같다. 감수하는 것: 호출자(survey-service·평가·게이트)는 항상 flow·fields를 만들어 보내야 한다. 이 변경은 I9 서버 호환을 깨므로 이 영역 버전을 어떻게 올릴지는 헤드가 정한다(이 브랜치에서는 올리지 않았다). 실제 모델·GPU 동작은 확인하지 못했다.

## 되돌리는 조건
계약이 칸 규격의 정본 데이터를 배포하면 측정 도구가 그것을 쓰게 바꾼다.
