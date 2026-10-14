# 런북 (model-runtime)

라이브러리이므로 서비스처럼 직접 운영하지 않는다. 아래는 이 라이브러리를 쓰는 서비스·평가에서 증상이 나올 때의 확인 순서다.

## 개발 시험
`.venv`를 만들고 `pip install pytest jsonschema -e ../boundary-contracts -e .`를 한 뒤 `.venv/bin/python -m pytest`. GPU 불필요.

## 증상별

| 증상(예외) | 뜻 | 확인 |
| --- | --- | --- |
| `ServingConfigMismatch` | 예외의 `role` 역할에 넘긴 `serving_config`의 해시가 그 역할 매니페스트의 `serving_config_hash`와 다르다 | 그 역할(`.role`) 매니페스트의 `serving_config`를 그대로 넘겼는지, 두 역할을 바꿔 넘기지 않았는지(키 순서는 무관, 값이 다르면 불일치). 설정을 고치지 말고 맞는 매니페스트를 쓴다. |
| `UnsupportedServingConfig` | 이 런타임이 모르는 템플릿 버전·`output_contract`·완화 정책·백엔드 | `RUNTIME_VERSION`과 매니페스트의 `lineage.model_runtime_version` 비교. 런타임을 올리거나 호환되는 릴리스를 쓴다. 릴리스 적재 중이면 이전 릴리스를 유지한다(I4). |
| `BackendNotAvailable` | 설정이 가리키는 백엔드가 이 환경에 없다 | GPU 이미지인지, 백엔드 extra가 설치됐는지. |
| `ContractVersionMismatch` | `schema_dir` 상위의 `CONTRACT_VERSION`이 지원 값과 다르거나 읽을 수 없다 | `schema_dir`가 `.../schema/schemas`(계약 JSON이 있는 디렉터리)인지, 그 상위에 `CONTRACT_VERSION`이 있는지. |
| `ContractViolation` | 입력이 입력 계약을 위반했다(모델 호출 전). `serving_config` 형식 위반. 또는 `load`에 넘긴 어댑터·설정·기대 해시의 역할 집합이 {LORA_A, LORA_B}와 다르다 | 예외의 (경로, 키워드) 목록으로 어느 필드인지 본다. 값은 담기지 않는다. |
| (예외 아님) 출력 보호가 자주 적용된다 | 모델 출력이 깨져 규칙이 정한 대체 출력이 나갔다. 파서는 항목 고침·빈 목록, 생성기는 `is_fallback = true` | `last_call_info.outcome`·`protections`(P1~P6, G0·G1·G4, G5)를 집계한다. 어느 규칙이 많은지 보고, model-lab이 `parse_raw`/`generate_raw`로 보호 전 품질을 잰다. `relaxed` 목록이 크면 백엔드가 제약을 놓치는 것이므로 `supported_features`를 점검한다. 어댑터·템플릿 문제는 model-lab에 알린다. |

## 하지 말 것
- 발화나 모델 출력 원문을 로그에 남기려고 이 라이브러리의 예외·로그를 확장하지 않는다.
- 운영 중에 `serving_config`를 임의로 바꾸지 않는다. 바꾸려면 새 모델 패키지(새 해시)를 만든다.
