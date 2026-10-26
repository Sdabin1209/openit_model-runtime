`schema/`는 `../survey-service/schema`의 `CONTRACT_VERSION` 사본이다(문진 스키마 정수 버전, D75). `schemas/`는 배포 디렉터리의 자리만 두고 비어 있다.
2.0.0부터 모델 입출력 스키마(`ActionExtraction` 등)는 이 디렉터리가 아니라 `boundary_contracts` 패키지에서 읽으므로 스키마 파일을 두지 않는다. 시험용이며 고치지 않는다.
작업공간에 원본이 있으면 `test_fixture_matches_workspace_schema`가 버전이 같은지 확인한다.
