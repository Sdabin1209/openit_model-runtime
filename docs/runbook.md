# 런북 (model-runtime)

라이브러리이므로 서비스처럼 직접 운영하지 않는다. 아래는 이 라이브러리를 쓰는 서비스·평가에서 증상이 나올 때의 확인 순서다.

## 개발 시험
`PYTHONPATH=src:tests:../boundary-contracts python3 -W error::DeprecationWarning -m unittest discover -s tests` (Python 3.10 이상, jsonschema 필요). GPU 불필요.

## 증상별

| 증상(예외) | 뜻 | 확인 |
| --- | --- | --- |
| `ServingConfigMismatch` | 넘긴 `serving_config`의 해시가 매니페스트의 `serving_config_hash`와 다르다 | 매니페스트의 `serving_config`를 그대로 넘겼는지(키 순서는 무관, 값이 다르면 불일치). 설정을 고치지 말고 맞는 매니페스트를 쓴다. |
| `UnsupportedServingConfig` | 이 런타임이 모르는 템플릿 버전·`output_contract`·완화 정책·백엔드 | `RUNTIME_VERSION`과 매니페스트의 `lineage.model_runtime_version` 비교. 런타임을 올리거나 호환되는 릴리스를 쓴다. 릴리스 적재 중이면 이전 릴리스를 유지한다(I4). |
| `BackendNotAvailable` | 설정이 가리키는 백엔드가 이 환경에 없다 | GPU 이미지인지, 백엔드 extra가 설치됐는지. |
| `ContractVersionMismatch` | `schema_dir`의 `CONTRACT_VERSION`이 지원 값과 다르거나 읽을 수 없다 | 호출자가 올바른 `schema_dir`(서비스는 자기 `schema/`)를 넘겼는지. |
| `SchemaDirRequired` | `schema_dir`를 안 넘기고 `configure`도 안 불렀다 | 진입점에서 `configure(schema_dir=...)` 또는 인자로 넘긴다. |
| `ContractViolation` | 입력이 입력 계약을 위반했다(모델 호출 전). 또는 `serving_config` 형식 위반 | 예외의 (경로, 키워드) 목록으로 어느 필드인지 본다. 값은 담기지 않는다. |
| `ModelOutputInvalid` | `max_attempts`까지 출력이 스키마를 통과하지 못했다 | `attempt_violations`로 위반 종류 확인(원문 없음). 반복되면 어댑터·템플릿·`supported_features` 완화 목록(`last_call_info.relaxed`)을 점검하고 model-lab에 알린다. 이 라이브러리는 대체 응답을 만들지 않으므로 호출자가 대체 경로를 정한다. |

## 하지 말 것
- 발화나 모델 출력 원문을 로그에 남기려고 이 라이브러리의 예외·로그를 확장하지 않는다.
- 운영 중에 `serving_config`를 임의로 바꾸지 않는다. 바꾸려면 새 모델 패키지(새 해시)를 만든다.
