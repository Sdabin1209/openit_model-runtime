# 변경 요청 002: vLLM 첫 시험에서 계약에 닿는 발견 (헤드에게)

작성: model-runtime 에이전트, 2026-10-15. 근거: gpu-dev(RTX 4070 SUPER 12GB)에서 vLLM 0.31.0 + xgrammar 0.2.7 + `Qwen/Qwen2.5-1.5B-Instruct` + 무작위 초기화 LoRA로 한 시험(`docs/design.md` §11 결과). 어댑터가 학습된 것이 아니므로 **출력 품질이 아니라 지원 기능·동작만** 근거다. 구현은 멈추지 않고 진행했다.

## 1. 스키마의 일부 규칙은 제약 디코딩이 지키지 못해 보호가 더 자주 개입할 수 있다
xgrammar는 `allOf`, `if/then`, `not`을 **오류 없이 조용히 무시**한다(제약이 느슨해질 뿐이다). `uniqueItems`는 오류로 거부한다. 따라서 `ParserOutput.Item`의 "`intent == ANSWERED_INTENT`이면 `target`·`value`가 null이 아니다"(`allOf`+`if/then`)는 제약으로 막을 수 없고 사후 검증(재시도) → 보호 P5가 담당한다. 문진 스키마를 바꾸자는 요청은 아니다. 알림이다: (가) 이 규칙을 `oneOf`/`anyOf` 가지로 쓰면 제약이 지킬 수 있는지 survey-service가 판단할 수 있다(가지가 객체인 `oneOf`는 이 시험에서 지켜졌으나 표본이 작다). (나) 스키마에 `uniqueItems`나 `allOf`/`if/then`/`not`을 더 넣으면 이 백엔드에서는 사후 검증에만 의존한다.

## 2. 어댑터 순위(rank)가 `max_lora_rank`를 넘으면 엔진 전체가 죽는다
`max_lora_rank`(gpu-dev 프로파일 16)보다 큰 순위의 어댑터를 요청하면 `ValueError` 뒤에 EngineCore가 종료되고 이후 모든 요청이 `EngineDeadError`다(이 프로세스는 복구하지 못한다). 어댑터는 `ModelPackageManifest`로 오므로 **model-lab이 매니페스트나 어댑터 설정에 순위를 기록**하면 로딩 전에 막을 수 있다. 요청: I3 `ModelPackageManifest`에 어댑터 순위(`lora_rank`)와 기반 모델 id·revision이 있는지 확인하고, 없으면 추가를 검토해 달라. 현재 model-runtime은 어댑터의 `adapter_config.json`(`r`, `base_model_name_or_path`)을 읽을 수 있으나 이는 PEFT 형식에 기댄 가정이다.

## 3. `serving_config.constraint`는 vLLM 버전과 지원 기능을 고정한다
`backend_version`에는 vLLM 정확한 버전(예 `0.31.0`)이, `supported_features`에는 `DEFAULT_SUPPORTED_FEATURES`가 들어가야 `load`가 받는다. vLLM·xgrammar 버전이 바뀌면 지원 기능과 출력이 달라질 수 있으므로 새 `serving_config`(새 해시)가 필요하다. 이미지 라벨의 model-runtime 버전과 함께 릴리스가 이 값을 승인 대상으로 보게 해 달라(알림).

## 4. 개정이 아닌 확인 요청
- 온도 > 0에서 같은 시드의 재현은 같은 서버에서는 지켜졌으나, **서버를 다시 띄우면 긴 출력에서 한 글자 단위로 달라지는 경우가 있었다**(3회 중 2회 불일치). 평가와 서비스의 결과 동일성을 "토큰 단위로 같다"로 약속하지 않는 것이 안전하다. 온도 0에서의 재현을 요구하는 수용 기준이 있으면 알려 달라.
