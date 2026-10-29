# ADR 0025: 영역 빌드 진입점(scripts/build-image.sh)과 계약 스키마 검증

## 상태
채택(2026-10-30, 헤드 지시 U42·U44, 계약 boundary-contracts v2.5.x의 `BuildImageRequest`/`BuildImageResult`).

## 맥락
1. **U42**: deploy의 `build-images.sh`가 이 영역의 Dockerfile 인자 이름(REVISION, BOUNDARY_VERSION, SURVEY_SCHEMA_VERSION, MODEL_RUNTIME_VERSION, VARIANT), 빌드 컨텍스트(`boundary`, `schema`), 라벨 값 만드는 법을 직접 알았다. 구조 규칙 1(영역은 서로의 구조를 모른다) 위반이다.
2. **U44**: 이 영역이 계약에 있는 스키마를 손으로 다시 썼다. `ServingConfig`(`serving_config.py`), `RuntimeProfile`(`vllm_backend.py`의 `_ENGINE_KEYS`와 모양 검사), `RuntimeFlowFile`(`backends.py`의 모양 검사와 `_REASK_REASONS`, `_KEY`). 계약이 바뀌면 사본이 어긋난다.

## 결정
1. `scripts/build-image.sh <요청 JSON>`이 영역 빌드 진입점이다. 요청은 계약 `BuildImageRequest`로 검증하고(계약 소스는 요청의 `dependency_sources.boundary`에서 읽는다), 표준 출력의 마지막 줄에 `BuildImageResult` 한 줄을 낸다(진행 기록은 표준 오류).
2. 이 영역이 쓰는 키가 요청에 없으면 실패한다: `versions.model_runtime`(라벨), `versions.survey_schema`(라벨), `dependency_sources.boundary`(빌드 컨텍스트 `boundary`), `dependency_sources.survey_schema`(내장 스키마, 빌드 컨텍스트 `schema`). `turn_engine`은 받기만 하고 쓰지 않으며 라벨은 `n/a`다(D109). 기본값을 영역 안에 두지 않는다. `Dockerfile`의 `ARG BOUNDARY_VERSION` 기본값은 개발 환경(`setup-env.sh`, `smoke.sh`)이 읽는 한 곳이고 이 진입점은 항상 요청 값을 명시해 넘긴다.
3. `variant`: 없으면 기본(CPU/가짜 백엔드), `gpu`면 `VARIANT=gpu`와 라벨 `io.openit.variant=gpu`. 그 밖의 값은 실패한다.
4. 거짓 라벨을 막는다. `versions.model_runtime`은 `source_dir`의 `pyproject.toml` 버전과 같아야 하고(이전에는 deploy가 빌드한 이미지에서 `RUNTIME_VERSION`을 읽어 확인했다), 빌드 뒤 이미지에 실제로 붙은 `io.openit.*` 라벨이 요청 값과 같아야 하며 계약 `ImageLabels`/`BuildImageResult`를 통과해야 한다.
5. U44: `ServingConfig`는 `validator("ServingConfig")`, 프로파일은 `validator("RuntimeProfile")`, 흐름 파일은 `validator("RuntimeFlowFile")`로 검증한다. 영역 안의 스키마 사본과 모양 검사를 지웠다. 스키마가 표현하지 못하는 항목 간 대응 하나(`corrections`/`reasks`의 key 문항이 `order`에 있어야 함)만 코드에 남겼다.

## 왜 (따른 표준과 근거)
- 구조 규칙 1·4·6과 R7: 영역 사이 입출력은 헤드가 책임지는 계약 스키마(`BuildImageRequest`/`BuildImageResult`)로만 만난다. 라벨 값과 이름을 deploy가 영역 소스에서 읽지 않고 영역이 낸 결과(이미지 라벨)를 읽는다.
- 같은 요청이 같은 이미지를 내도록 인자와 컨텍스트를 요청에서만 받는다(이웃 리포 경로·빌드 중 네트워크 없음).
- 검증은 계약 패키지의 검증기를 쓴다: 사본이 계약과 어긋나지 않는다.

## 검토한 대안과 버리는 이유
- deploy가 계속 Dockerfile 인자를 안다: 구조 규칙 1 위반. 기각.
- 라벨을 deploy가 만들어 넘기고 영역은 검사만: 라벨 이름·값 만드는 법을 deploy가 알게 된다. 기각.
- `survey_schema`를 선택으로 두기: 이 영역은 스키마를 이미지에 내장하는 것을 기본으로 한다(D72). 선택으로 두면 요청에 따라 이미지 내용이 갈린다. 필수로 했다.

## 결과
얻는 것: deploy는 영역 내부를 읽지 않고 요청과 결과만 다룬다. 스키마 사본이 사라졌다. 감수하는 것: 호스트 파이썬에 `jsonschema`가 있어야 한다(`PYTHON`으로 지정). 프로파일 최상위의 모르는 키와 흐름 파일의 모르는 키(`skip` 같은 오타)는 이제 거부된다(옛 코드는 무시했다). 기존 프로파일 파일은 모두 통과함을 시험으로 확인했다. gpu 이미지의 실제 빌드(약 13GB)는 하지 않았다. 인자 전달은 가짜 도커로 시험했다.

## 되돌리는 조건
계약의 빌드 진입점 형식이 바뀌면 따른다.
