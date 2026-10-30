# ADR 0028: 영역 구조를 컴포넌트·스키마·wiring으로 (D117, U400)

## 상태
채택(2026-10-30, 헤드 지시 U400, `head/docs/DESIGN.md`). [ADR 0017](0017-role-components-and-shared-retry.md)(역할 컴포넌트와 공용 재시도)를 이어 받아 영역 전체로 넓힌다.

## 맥락
구조 규칙 6개(D104)를 코드 배치로 옮기라는 지시다. 이 영역은 `src/model_runtime/` 아래 평평한 모듈이었다(session, roles, prompts, schema_guard, server 등). 입력 검사가 모듈마다 코드 안에 흩어져 있었고(`if`/`raise`), 서버 파일 하나가 HTTP, 릴리스 상태, 추론 직렬화, 설정 읽기를 함께 했다.

## 결정
1. 이 영역의 일(프롬프트 조립, 입출력 검증, 제약 스키마 완화, 서빙 설정 검사, 재시도, LoRA-A·B 역할, 백엔드 셋, 릴리스 레지스트리, 통신)을 업무 단계로 나눠 컴포넌트 19개로 한다(`docs/components.md`). 컴포넌트마다 `schemas/`의 입력·출력 스키마 이름을 `INPUT, OUTPUT`으로 선언한다.
2. 입력·출력 검사는 wiring의 `schema_gate.gate`가 컴포넌트 앞뒤에서 `schemas/`로 한다. 스키마로 쓸 수 없는 8건은 `schemas/README.md`의 "스키마 밖 조건" 표에 이유와 함께 둔다.
3. 컴포넌트끼리는 import하지 않는다. 다른 컴포넌트의 일이 필요하면(재시도, 출력 검증, 대체 문장, 세션 적재) wiring이 함수로 주입한다.
4. 받는 쪽 통신은 통신 기반 라이브러리 `openit_link`(D118) 위의 `link_receive`, 이웃에서 읽는 쪽은 `link_release_fetch`. 인증 401/403·본문 상한·계약 검증·`BoundaryError`·`/healthz`·`/readyz`는 라이브러리가 한다.
5. 공개 이름(다른 영역이 import하는 이름)은 바꾸지 않는다. `model_runtime.prompts`, `model_runtime.schema_guard`는 `__init__.py` 하나뿐인 패키지로 남겨 새 위치에서 다시 내보낸다. HTTP 경로·상태 코드·본문·헤더·이미지 라벨 키도 그대로다. 패키지 버전은 0.11.3(PATCH)이다: 서버가 MAJOR.MINOR로 매니페스트의 `model_runtime_version`을 비교하므로 MINOR를 올리면 이미 만든 번들이 거절된다.
6. `types/`는 `datamodel-code-generator` 0.87.0(`requirements-codegen.lock`)으로 `schemas/`에서 생성한다(`scripts/gen-types.sh`).

## 왜
`head/docs/DESIGN.md` 2·3·5·6절(functional core / imperative shell, 포트·어댑터). 스키마가 진실이면 거부 조건을 한 곳에서 읽고 시험할 수 있다. 바깥 자원(백엔드, 시계, 릴리스 서버, 파일)을 인자로 받으면 업무 컴포넌트를 가짜 없이 시험할 수 있다. 값(0.11.3 등)은 측정이 아니라 규칙(위 5번)에서 나왔다.

## 검토한 대안과 버리는 이유
- 모든 컴포넌트가 생성 타입(dataclass)을 입출력으로 쓰기: 역할·설정이 열거형·중첩 객체로 바뀌어 공개 타입(`CallInfo`, `GenerationRequest`)의 모양이 달라진다. 컴포넌트는 스키마 검증을 통과한 JSON 모양(dict)으로 주고받고, 생성 타입은 공개 결과 타입에만 쓴다.
- 검증을 컴포넌트마다 코드에 두기: DESIGN.md 5절이 금지한다. 스키마를 읽는 코드가 컴포넌트마다 중복된다.
- 공유 코드(예외 어휘, 포트, 스키마 문지기)를 컴포넌트 밖 어디에도 두지 않기: 6절 6항이 허용하는 곳은 components/wiring/types/진입점뿐이다. 영역 공용 어휘라 wiring에 둔다.

## 결과
얻는 것: 거부 조건이 스키마에 있고, 컴포넌트가 서로를 모르고, HTTP 규약이 한 곳이다. 감수하는 것: `/readyz`의 사유는 인증을 켠 실행에서 응답이 아니라 로그로만 낸다(라이브러리의 `/readyz`는 인증하지 않는다, 변경 요청 CR-007), 존재하지 않는 경로를 인증 없이 부르면 401이 아니라 404다. 실제 모델·GPU 동작은 이 변경으로 확인하지 못했다.

## 되돌리는 조건
통신 기반 라이브러리가 `/readyz` 사유를 인증된 호출자에게 줄 수 있게 되면 로그만 남기는 부분을 되돌린다.
