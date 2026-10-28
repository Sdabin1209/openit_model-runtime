# CR-006: model-runtime 영역 경계 형식의 스키마 초안 (헤드에게)

작성: 2026-10-28, wo2 브랜치. 근거: 지시서 1 B, 자기 점검 보고서 E절(MR-09, MR-10, MR-07). **코드는 바꾸지 않았다.** 스키마를 계약(boundary-contracts)에 올릴지, 올린다면 어느 이름·위치인지는 헤드가 정한다. 올라가면 이 영역이 코드에서 이 스키마를 쓰도록 바꾼다(다음 단계).

초안은 `docs/change-requests/schemas/*.schema.json`(JSON Schema 2020-12), 예시는 `schemas/examples/<이름>.valid.json`(실제 코드·설정에서 만든 것)과 `<이름>.invalid.json`(거부되어야 하는 것)이다.

## 1. ServingConfig (받는 쪽: model-runtime이 `load`에서 읽는다)
- 누가 만들고 읽는가: model-lab이 모델 패키지에 쓰고, release-gate가 ReleaseManifest에 그대로 담고, model-runtime이 읽는다. 지금 계약의 `serving_config`는 "형식은 model-runtime이 정한다"는 자유 객체이고(MR-09), 형식의 정본은 `src/model_runtime/serving_config.py`의 `SERVING_CONFIG_SCHEMA`다.
- 예시: `examples/ServingConfig.valid.json`(`mr.fake_serving_config()`가 내는 값).
- 모르는 키 정책: **거부**. 해시가 받은 객체 그대로의 JCS 해시라 모르는 키를 무시하면 해시와 동작이 갈라진다.
- 불변식: 해시는 기본값을 채우지 않고 받은 그대로 계산한다. `output_contract`, `prompt_template_version`, `protection_policy_version`, `constraint.relax_policy_version`는 이 영역이 지원하는 값이어야 적재된다(스키마로 표현하지 못하고 `/v1/info`의 `serving_config_expectation`이 알려 준다). `constraint.backend`는 서버의 백엔드 이름(`vllm`, `fake`, `mimic`)과 같아야 한다.

## 2. RuntimeInfo (내는 쪽: GET /v1/info 응답)
- 누가: model-runtime이 만들고 deploy·model-lab·survey-service가 읽는다. HTTP.md I9는 6필드만 정하고 `system_prompt_sha256`, `serving_config_expectation`, `test_hooks`는 이 영역이 더했다(MR-10 1항).
- 예시: `examples/RuntimeInfo.valid.json`(`build_service`로 띄운 서버의 `info()`).
- 모르는 키 정책: 최상위는 **무시**(내는 쪽이 필드를 더해도 읽는 쪽이 깨지지 않게). `serving_config_expectation` 안은 **거부**(그 값에 맞춰 설정을 만들기 때문에 키 이름이 틀리면 조용히 어긋난다).
- 불변식: 적재된 릴리스가 없으면 `serving_config_hash`, `system_prompt_sha256`은 `{}`이고, 있으면 `LORA_A`, `LORA_B` 둘 다 있다. 백엔드가 아직 안 떴으면 `serving_config_expectation`은 null. `backend`는 서버가 고른 이름 그대로.

## 3. ReleaseLoadStatus (내는 쪽: PUT/GET /v1/releases/{id}의 상태 본문)
- 누가: model-runtime이 만들고 survey-service와 deploy가 읽는다. HTTP.md I9 표에만 있고 스키마 파일이 없다(E절).
- 예시: `examples/ReleaseLoadStatus.valid.json`(`status_body`가 내는 ABSENT 상태). FAILED 상태는 `verify.py`가 `BoundaryError(...).to_record()`로 만든다.
- 모르는 키 정책: **거부**(작고 닫힌 상태 표시).
- 불변식: `state`가 FAILED일 때만 `error`가 객체(BoundaryError)이고 그 밖에는 null. `release_id`는 서버의 ID 규칙(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)을 따른다.

## 4. RuntimeProfile (받는 쪽: profiles/*.json)
- 누가: 이 영역이 하드웨어별로 두고(`gpu-12g`, `rtx4090-24g`, `gpu-dev-12g`) deploy가 `MODEL_RUNTIME_PROFILE`로 고르며 vLLM 백엔드가 읽는다.
- 예시: `examples/RuntimeProfile.valid.json`(`profiles/gpu-12g.json` 그대로).
- 모르는 키 정책: **거부**. 현재 코드(`load_profile`)는 `engine`의 모르는 키만 거부하고 최상위의 모르는 키는 무시한다. 오타가 조용히 사라지지 않도록 최상위도 닫는 것이 제안이며 **지금 코드보다 엄격하다**(주석은 `_`로 시작하는 키로 쓴다).
- 불변식: `engine` 값의 형은 vLLM 엔진 인자와 같다. 두 어댑터를 한 엔진에 올리므로 `max_loras`는 2 이상이어야 하나 스키마로 강제하지 않았다(코드가 `setdefault`로 2를 준다). `env`의 값은 문자열이고 `_` 키는 환경에 쓰지 않는다.

## 5. RuntimeFlowFile (받는 쪽: 가짜·모방 백엔드의 문항 흐름 파일)
- 누가: deploy·survey-service의 시험 설정이 만들고 model-runtime의 fake·mimic 백엔드가 읽는다(`MODEL_RUNTIME_FAKE_FLOW_FILE`, `MODEL_RUNTIME_MIMIC_FLOW_FILE`). 문항 순서·건너뛰기는 survey-service/turn-engine 쪽 정본의 복사본이라 어긋날 수 있다(MR-07).
- 예시: `examples/RuntimeFlowFile.valid.json`(`tests/test_fake_flow.py`의 `V2_ORDER`, `SLOTS`, `SKIPS`, `CORRECTIONS`와 되묻기 한 건).
- 모르는 키 정책: 객체의 모르는 키는 **거부**(지금 코드는 무시하며 `skip` 같은 오타가 사라진다, 코드보다 엄격). 문항 ID 목록(배열) 형식도 허용한다.
- 불변식: `order`는 중복 없는 문항 ID. `slots`·`corrections`·`reasks`의 키는 모델 키 문법. `corrections`/`reasks`의 키가 가리키는 문항은 `order` 안에 있어야 하고 `skips.skip_item`도 그렇다(스키마로 표현하지 못한다. 코드가 읽을 때 확인한다).

## 스키마를 쓰지 않은 경계 형식 (이유)
- 토큰 파일 형식 `역할=토큰,...`: JSON이 아닌 한 줄 텍스트라 JSON Schema로 쓸 수 없다. 규칙은 `docs/boundary.md`에 있다(MR-10 4항).
- `X-Openit-Protections`의 코드 값(`G0`, `G5`): 헤더 문자열이며 HTTP.md가 이 영역에 위임했다.
- `MODEL_RUNTIME_*` 환경 변수 이름, Dockerfile 빌드 인자·컨텍스트 이름, 이미지 라벨: 스키마 대상이 아니라 배포 인터페이스 표(runbook)다. 헤드가 deploy와 표를 맞춰야 한다.
- GPU 세션 키트의 번들 디렉터리(`prepare_bundle.py`, `release_server.py`, MR-10 7항): 운영 경로가 아닌 키트 내부 재구현이다. 계약의 I4 서버/번들 형식을 쓰도록 바꾸는 문제이지 새 스키마 문제가 아니다.
- 모방 규칙표 `data/mimic_rules.json`: 이 영역 안에서만 읽고 쓰는 파일이라 경계 형식이 아니다.

## 검증 (실제로 돌린 것)
```
.venv/bin/python docs/change-requests/schemas/verify.py
```
```
ServingConfig: valid 예시 위반 0건, invalid 예시 위반 1건 (Additional properties are not allowed ('temprature' was unexpected))
RuntimeInfo: valid 예시 위반 0건, invalid 예시 위반 1건 ('backend' is a required property)
ReleaseLoadStatus: valid 예시 위반 0건, invalid 예시 위반 1건 ('READY' is not one of ['ABSENT', 'LOADING', 'LOADED', 'FAILED'])
RuntimeProfile: valid 예시 위반 0건, invalid 예시 위반 1건 (Additional properties are not allowed ('max_model_length' was unexpect)
RuntimeFlowFile: valid 예시 위반 0건, invalid 예시 위반 1건 ({'order': ['Q1.D01', 'Q1.D02', 'Q1.D03', 'Q1.D04', 'Q4', 'Q4_1', 'Q5',)
ReleaseLoadStatus FAILED 예시(error는 BoundaryError 검증기 포함): 위반 0건
exit=0
```
`verify.py`는 각 스키마가 유효한 스키마인지(`check_schema`), valid 예시가 통과하고 invalid 예시가 거부되는지 본다. 이 예시는 이 영역 코드가 만든 값이므로 "코드와 스키마가 같은 말을 한다"는 확인이지 이웃이 읽을 수 있다는 확인은 아니다(이웃 쪽 검증은 하지 않았다).
