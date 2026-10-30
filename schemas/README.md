# schemas (영역 내부 타입)

이 영역의 컴포넌트 입출력 타입이다(JSON Schema draft 2020-12). 타입 하나에 파일 하나, 파일 이름이 타입 이름이다. 통과·거부 예시는 `examples/<타입>.valid*.json`, `<타입>.invalid*.json`이고 `tests/test_structure.py`가 결과를 확인한다. 영역 사이 타입(ActionExtractionInput, ActionExtraction, ResponseRequest, ResponseText, ServingConfig, ExtractRequest, RuntimeFlowFile, RuntimeProfile 등)은 복사하지 않고 boundary-contracts를 쓴다. 컴포넌트와 타입의 대응은 `docs/components.md`.

생성 코드 타입은 `src/model_runtime/types/`(손으로 고치지 않는다, 생성 명령은 README 4절).

## 스키마 밖 조건

스키마로 표현하지 못해 컴포넌트 안에 남은 거부 조건이다. 모두 필드 사이 대응이거나 바깥 상태에 달린 것이다.

| 컴포넌트 | 조건 | 스키마로 못 쓰는 이유 |
| --- | --- | --- |
| serving_config_check | 설정의 `constraint`(backend, backend_version, supported_features)가 사용할 백엔드와 같아야 한다 | 입력 안의 두 필드를 서로 비교하는 조건이다(JSON Schema에는 필드 간 동등 비교가 없다) |
| serving_config_check | 계산한 해시가 기대 해시와 같아야 한다 | 해시는 설정에서 계산되는 값이라 입력 스키마가 알 수 없다 |
| system_prompt_read | 파일이 있어야 하고, 어댑터 디렉터리 밖을 가리키면 안 되며, 파일 내용의 해시가 `sha256`과 같아야 한다 | 파일 시스템 상태와 파일 내용에 달려 있다 |
| flow_file_load | `corrections`·`reasks`의 `key` 문항이 `order`에 있어야 한다 | 배열 항목의 값이 같은 문서의 다른 배열 원소여야 한다는 조건이다(`enum`으로 쓰려면 문서마다 스키마가 달라진다) |
| token_table | 역할이나 토큰이 중복되면 안 된다 | 객체 값의 유일성을 JSON Schema로 쓸 수 없다 |
| runtime_dirs_check | 디렉터리를 만들고 쓸 수 있어야 한다 | 파일 시스템 상태다 |
| vllm_backend | 어댑터 순위 r이 엔진의 `max_lora_rank` 이하여야 하고, 어댑터의 기반 모델이 사용할 기반 모델과 같아야 한다 | 어댑터 설정과 엔진 설정 두 문서 사이의 비교다 |
| release_registry | 릴리스가 LOADED여야 추론할 수 있고, 대기가 길면 거절하고, 입력 JSON 직렬화가 `MODEL_RUNTIME_MAX_INPUT_CHARS` 이하여야 한다 | 서버의 시간·상태에 달린 거절이다(길이는 직렬화 결과의 길이라 스키마의 `maxLength`로 쓸 수 없다) |

위 8건이 목표 0에서 남은 것이다.
