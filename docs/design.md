# model-runtime 설계 (현행)

현행 API는 `extract`(`ActionExtractionInput` -> `ActionExtraction`, JSON)와 `respond`(`ResponseRequest` -> `ResponseText`, 문장 그대로의 텍스트)다. 버전은 `src/model_runtime/session.py`의 `RUNTIME_VERSION`, 계약 태그는 `deploy/scripts/lib.sh`의 `BOUNDARY_TAG`가 정본이다. 입출력 스키마는 `boundary_contracts` 패키지(`load_schema`, `validator`)에서 읽는다. 호출 경로의 세부는 [boundary.md](boundary.md), 운영은 [runbook.md](runbook.md)에 있다.

| 항목 | 현행 |
| --- | --- |
| 호출 | `extract`(`ActionExtractionInput` -> `ActionExtraction`, JSON), `respond`(`ResponseRequest` -> 문장 그대로의 텍스트) |
| 모델 입출력 스키마 | `boundary_contracts` 패키지(`load_schema`, `validator`). `schema_dir`는 `CONTRACT_VERSION`(문진 스키마 정수 버전, D75)만 확인 |
| 시스템 프롬프트 | **모델 패키지의 파일**(어댑터 디렉터리, 해시 `system_prompt.sha256`). 코드는 조립 규칙(`prompt-4`; `prompt-3`은 0.7.0에서 제거)만 갖는다. `load(..., system_prompts=)`가 읽고 해시를 확인한다 |
| 채팅 형식 | Qwen3 ChatML, `enable_thinking=False`(`<think>\n\n</think>\n\n`를 어시스턴트 시작에 붙임). 입력 직렬화는 `prompt-4`(D77, 학습 데이터와 같은 `json.dumps(ensure_ascii=False)`; A는 최상위만 정렬, B는 전 수준 정렬). 학습 val 대조와 chat_template 렌더 대조는 확인됨, 학습 때 쓴 채팅 템플릿 설정은 미확인(prompts.py 머리말) |
| extract 실패 | 보호 없음. `ModelOutputInvalid`(HTTP 503 `OUTPUT_INVALID`). 값을 지어내지 않는다(D75) |
| respond 실패 | 재료(`known`, `ask`, `hold`, `next.text`)를 이은 대체 문장, `is_fallback`/`X-Openit-Outcome: fallback`. 정책 `protect-2` |
| 제약 디코딩 | A만(`ActionExtraction`을 `relax-3`으로 완화, 0.6.5·D83). B는 텍스트라 제약 없음. action별·status별 `allOf/if/then`은 xgrammar가 부분 지원이라 같은 뜻의 `anyOf` 변형으로 펼쳐 보내고(`relax-2`까지는 제거), 사후 검증은 그대로 한다. `pattern`·`minItems`는 xgrammar 0.2.7이 강제하는 것을 컨테이너 안 시험으로 확인해(헤드) 0.6.4부터 보낸다(relax-1은 이 둘을 빼서 key·targets·next_item이 제약되지 않았다) |
| 프로파일 | `gpu-12g`=Qwen3-4B, `rtx4090-24g`=Qwen3-8B(둘 다 적재 가능 여부·A+B 동시 적재 미확인). 어댑터와 프로파일의 기반 모델이 다르면 적재 거부 |
| 서버 | `.../extract`(JSON), `.../respond`(`text/plain`), `X-Openit-Protections`는 보호가 있을 때만, `/v1/info`에 `system_prompt_sha256` 추가 |
| fake | 스위치 없음(환경 변수가 남아 있으면 시작 거부). 기본 출력은 `ActionExtraction` 최소값과 `(가짜 응답)` |

열린 항목: 현행 목록은 [STATUS.md](STATUS.md)와 `docs/change-requests/`가 정본이다.

## 이력

처음 설계(v1: `parse`/`generate`, `ParserOutput`/`GeneratorOutput`)와 GPU 시험 기록(§11)은 이력 문서 [history/design-v1.md](history/design-v1.md)로 옮겼다. 그 본문은 v1 기준이므로 현행 동작의 근거로 쓰지 않는다. 백엔드 구조, 완화, 재시도, `serving_config` 해시, 역할별 설정은 거기 서술이 이어지지만 현행 값은 코드와 ADR(`docs/adr/`)이 정본이다.
