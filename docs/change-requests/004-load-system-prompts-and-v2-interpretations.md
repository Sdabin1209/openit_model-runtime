# CR-004: I9 `load`의 시스템 프롬프트 인자와 v2 해석 확인 요청 (model-runtime -> 헤드)

상태: 요청(2026-10-27). 계약 2.0.0(태그 v2.0.0)을 읽고 0.6.0을 만들면서 계약 문서만으로 정할 수 없던 것이다. 이 영역은 계약을 고치지 않고 아래대로 구현했다. 확인된 것과 제안을 구분한다.

## 1. `load`에 시스템 프롬프트 인자 (확인: 문서에 없음 / 제안)
- 확인: INTERFACES I9의 `load(adapters, serving_configs, *, schema_dir, expected_serving_config_hashes, backend=None)`에는 시스템 프롬프트를 받는 자리가 없다. 같은 절은 "model-runtime은 서빙할 때 이 패키지의 프롬프트만 쓴다"고 하고, `render_prompt`는 `system_prompt` 문자열을 받는다. 프롬프트 파일의 `path`와 `sha256`은 `ReleaseManifest.adapters[role].system_prompt`에만 있어 라이브러리가 어댑터 디렉터리만 보고는 알 수 없다.
- 구현: `load(..., system_prompts: Mapping[role, {path, sha256}])` 키워드 인자를 필수로 추가했다. 파일을 `adapters[role]` 안에서 읽어 `canonical.file_hash`로 확인하고(없음·디렉터리 밖·해시 다름·빈 파일은 `SystemPromptMismatch`) 서버는 이를 422 `INTEGRITY_ERROR`로 낸다. `FakeModelSession`은 문장 문자열을 `system_prompts=`로 받고, 없으면 시험용 고정 문장을 쓴다.
- 요청: INTERFACES I9의 `load` 시그니처에 `system_prompts`를 반영하거나 다른 방식을 알려 달라. 바뀌면 호출자(model-lab 평가, survey-service/deploy의 서버 외 사용)에 영향이 있다.

## 2. 채팅 형식과 입력 직렬화 (미확인)
- 구현: `prompt-3` = Qwen3 ChatML(`<|im_start|>system ... <|im_start|>user {입력 JSON} ... <|im_start|>assistant\n<think>\n\n</think>\n\n`), 입력 JSON은 `canonical.canonicalize`(JCS: 키 사전순, 공백 없음, UTF-8).
- 미확인: ① 이 문자열이 Qwen3 토크나이저의 `apply_chat_template(enable_thinking=False)` 결과와 같은지(토크나이저 파일이 없어 대조하지 못했다). ② 학습 데이터의 user 메시지가 입력 JSON을 같은 방식으로 직렬화했는지(들여쓰기, 키 순서, `ensure_ascii`; 학습 서버 데이터는 이 영역이 읽을 수 없다). 어긋나면 학습-서빙 프롬프트가 달라져 품질이 떨어진다.
- 요청: 학습 데이터 한 건의 user/system 메시지 원문(개인정보 없는 합성 표본)과 학습 때 쓴 채팅 템플릿 설정을 model-lab 또는 데이터셋 담당에게서 받아 달라. 받으면 `prompt-4`로 맞추고 골든 시험을 그 표본으로 바꾼다.

## 3. respond 대체 문장 (제안, 계약 밖)
- 계약: "입력의 재료(`known`, `ask`, `hold`, `next.text`)를 이어 붙여 읽는 문장 ... 규칙은 model-runtime이 정한다". 지시서: B의 대체 문장 규칙은 이 영역이 정한다.
- 구현(`protect-2`의 J1~J2): 순서 `known`, `ask`, `hold`, `next.text`, 비어 있지 않은 것만 공백 하나로 잇기, 바로 앞과 같은 조각은 한 번만, 재료가 없으면 "죄송합니다. 다시 한 번 말씀해 주세요."(잠정, 환자에게 가는 문구라 도메인 소유자 확인 필요). `END`인데 `known`이 빈 경우처럼 재료가 없으면 이 문구가 나가 어색하다(제안: END 전용 문구를 도메인 소유자가 정한다).
- 제안(미구현): v1의 G1에 해당하는 검사(응답의 숫자가 입력 재료에 있는지)를 v2에서 둘지. 지금은 두지 않았다. 둘 경우 허용 범위(`user_utterance` 포함 여부)를 정해 달라. 지시서의 "미정"에 이 항목은 없어서 구현하지 않고 여기에 적는다.

## 4. 알림 (변경 없음, 참고)
- `/v1/info`에 `system_prompt_sha256`(가장 최근 적재 릴리스의 패키지 프롬프트 해시)을 추가 필드로 냈다. LoRA 교체(D72)가 프롬프트까지 바뀌었는지 deploy가 확인하기 쉽다. 계약 목록에는 없는 추가이므로 거슬리면 알려 달라.
- 환경 변수 `MODEL_RUNTIME_FAKE_RISK_PHRASE`와 fake의 위험 신호 출력을 없앴다. v2 출력(`ActionExtraction`)에 위험 신호 표현이 없고 survey-service 쪽이 "미정"이라서다. deploy의 E2E가 이 스위치를 쓰고 있으면 알려 달라(대체 방법은 survey-service의 결정에 달렸다).
- `boundary_contracts` 패키지(`pip install ...@v2.0.0`)에는 `conformance` 모듈이 없다(`packages`에 `boundary_contracts`, `boundary_contracts.clients`만 있다). 이 영역의 적합성 시험은 형제 폴더 `../boundary-contracts`(태그 v2.0.0과 같은 내용임을 시험이 확인)의 `conformance.servers.ReferenceServer`를 읽기 전용으로 import한다.
