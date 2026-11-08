# ADR 0038: 계약 5.0.0 반영 — ID 위임, 본문 상한은 계약 경로 값, 모델 출력 파서

## 상태
채택(2026-11-08, 헤드 지시 S3, 계약 v5.0.0 CHANGELOG·CONVENTIONS 10절). [ADR 0012](0012-security-and-input-defaults.md)의 본문 4MiB 근거를 보완한다(값은 그대로). 모델 입출력 모양은 바뀌지 않는다.

## 맥락
계약 5.0.0은 (1) ID 칸을 공통 타입(`_common.Id` 등)으로 통일하고 영역의 사본을 금지한다(D117), (2) 영역 사이 JSON에 공통 규칙(UTF-8, 중복 키 없음, NaN·Infinity·1e999 거부, 정수 ±2^53, 깊이 64, 본문 1 MiB)을 둔다. 이 영역에는 `PutRequest`·`InferRequest`의 `release_id` 패턴 사본, 4MiB 본문 상한, 자체 `json.loads` 모델 출력 파서가 있었다.

## 결정
1. `schemas/PutRequest.json`, `schemas/InferRequest.json`의 `release_id`는 패턴 대신 `"$ref": "../boundary/_common.schema.json#/$defs/Id"`로 계약 `Id`에 위임한다. 계약 문서는 `boundary_contracts.load_schema("_common")`로 읽어 `wiring/schema_gate.py`의 레지스트리에 그 이름으로 등록한다(복사하지 않는다).
2. 본문 상한 `MAX_BODY`는 4MiB 그대로다. 계약 openapi(model-serving)가 PUT·extract·respond의 `x-max-body-bytes`를 4194304로 묶고, 서버 시작 때 `HttpContract.verify_route`가 이 값을 대조한다(1 MiB로 바꾸면 시작이 거부된다, 시험으로 확인). 근거가 '기록 없음'이던 값이 이제 계약 값이다.
3. `extract_role._parse`(모델 출력 파싱)는 `strict_json.loads`를 쓴다. 자체 `parse_constant` 거부를 지운다.
4. 입력 2500자 상한(`MODEL_RUNTIME_MAX_INPUT_CHARS`)은 계약에 없는 영역 내부 정책이라 코드에 남기고, `schemas/README.md`의 '스키마 밖 조건' 표에 근거를 적는다.

## 왜
- 따른 표준: 계약 5.0.0 CHANGELOG, CONVENTIONS 10절(계약 JSON 공통 규칙), D117(같은 뜻의 타입은 한 번만).
- 사본의 차이: 영역 패턴은 후행 개행을 막는 `(?![\s\S])`가 없었다(계약 `Id`는 있다). 위임으로 `"ok\n"` 같은 값이 거부된다(의도된 좁힘).
- 4MiB: 계약 CONVENTIONS 10절은 JSON 본문 1 MiB를 정하지만 같은 계약의 model-serving 경로 묶음은 4194304다. 두 값이 어긋난다. 이 영역은 경로 묶음을 따른다(서버가 시작 때 대조하므로 다른 값은 불가능). 어긋남은 헤드에게 알린다(풀지 못한 것). 실제 입력은 2500자 상한이 먼저 걸려 어느 쪽에도 닿지 않는다. 측정하지 않았다.
- 모델 출력 파서: 모델이 낸 `1e999`·큰 정수·중복 키를 영역이 따로 판정하지 않고 계약 정본에 맡긴다.
- 2500자: 계약에 입력 전체의 길이 상한은 없다(발화 maxLength 1000만). 근거는 ADR 0012(max_model_len, 1자=1토큰 가정, 측정 안 함). 계약상 유효한 입력이 2500자를 넘어 400이 될 수 있다는 점은 그대로이며 실제 모델 길이로 재지 않았다.

## 검토한 대안과 버리는 이유
- `x-contract` 위임: 이 영역은 자체 `Draft202012Validator`로 영역 스키마를 검증하고 `x-contract`를 해석하는 코드가 없다. 실제로 해석되는 `$ref`가 있으므로 그것을 쓴다.
- 4MiB 유지: 위 어긋남. 기각.
- 2500자를 계약으로 올리기: 모델 컨텍스트에 달린 영역 내부 정책이다. 변경 요청은 내지 않는다(되돌리는 조건 참고).

## 결과
얻는 것: ID 사본과 자체 파서가 사라지고 계약 값이 한 곳이다. 감수하는 것: 1~4MiB 본문은 계약 JSON 규칙(1 MiB)과 경로 묶음(4MiB) 사이 어긋남에 놓인다(위). 영역 스키마 검증기는 아직 jsonschema `Draft202012Validator`를 직접 만든다(영역 내부 스키마는 계약 `validator(name)`이 이름으로만 받아 쓸 수 없다).

## 되돌리는 조건
계약이 입력 길이 상한을 정하면 2500자 코드 거부를 지운다. 계약이 영역 스키마를 검증기에 싣는 공개 방법을 주면 직접 생성을 없앤다.
