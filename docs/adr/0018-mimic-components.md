# ADR 0018: 데모용 모방 컴포넌트(MimicExtractor, MimicResponder)와 규칙표 데이터 파일

## 상태
채택(wo2 브랜치, 지시서 2 데모 요구).

## 맥락
데모는 모델 서빙 자리의 LoRA-A·LoRA-B만 모방으로 바꾸고 나머지는 진짜 코드로 돌려야 한다. 기존 fake는 값 채우기·정정·되묻기 정도만 내고, 응답은 고정 문장 하나다.

## 결정
`MODEL_RUNTIME_BACKEND=mimic`(+ `MODEL_RUNTIME_ALLOW_FAKE=1`)로 `MimicBackend`를 쓴다. 백엔드 이름은 `mimic`이라 `/v1/info`의 `backend`와 `serving_config_expectation.constraint.backend`에 그대로 보인다. `MimicExtractor`(ActionExtractionInput 읽기 -> ActionExtraction)와 `MimicResponder`(ResponseRequest 읽기 -> 문장)는 서로를 모르는 두 컴포넌트다. 문항 순서·칸 정의는 fake와 같은 흐름 파일 형식(`order`, `slots`, `skips`, `corrections`, `reasks`)을 받는다(`MODEL_RUNTIME_MIMIC_FLOW_FILE`, 없으면 `MODEL_RUNTIME_FAKE_FLOW_FILE`). 규칙(낱말 목록, 문장 틀)은 코드가 아니라 `src/model_runtime/data/mimic_rules.json`(또는 `MODEL_RUNTIME_MIMIC_RULES_FILE`)이다.

## 왜
- 결정적이어야 한다는 데모 요구: 난수·시간을 쓰지 않고 문장 고르기는 입력 JSON의 sha256으로 한다.
- 규칙표를 데이터로 둔 이유: 낱말·문장은 데모 담당이 고치는 일이 잦고, 고칠 때 코드 변경과 이미지 재빌드를 피하려는 사용자 요구(규칙표는 데이터 파일). 형태 검사는 `load_rules`가 한다.
- fake와 이름을 나눈 이유: "고정 응답 가짜"와 "입력에 따라 달라지는 모방"을 `/v1/info`에서 구분해야 데모와 시험을 헷갈리지 않는다.
- 값을 지어내지 않는 이유: 모방도 모델이 아니다. 칸 정의가 없는 문항은 값을 내지 않고, 모를 때는 되묻기·보류로 간다. 응답 문장은 절차 안내만 하고 임상 조언 낱말을 쓰지 않는다(시험이 금지 낱말을 검사한다).

## 검토한 대안과 버리는 이유
- 기존 fake 확장: 고정 문장 응답과 첫 칸만 쓰는 규칙에 더하면 fake 시험이 복잡해진다. 따로 둔다.
- 작은 실제 모델: 데모 요구(결정적, 모델 서빙 자리만 교체)와 맞지 않고 GPU가 필요하다.

## 결과
얻는 것: 일곱 동작(값 채우기·되묻기·확인·정정·건너뛰기·보류·다른 주제 이동)과 종료(END)를 내는 결정적 대역. 감수하는 것: 낱말 규칙은 한국어 부분 문자열 일치라 오판이 있다(예: "네"가 든 낱말). 측정하지 않았고 데모 품질 용도다. 문구는 임상 검토를 받지 않았다. 배포 쪽 릴리스의 `serving_config.constraint.backend`는 `mimic`이어야 적재된다(`/v1/info`의 expectation을 따른다).

## 되돌리는 조건
데모가 진짜 모델로 대체되면 이 컴포넌트와 규칙표를 지운다(운영 경로에 영향 없음).
