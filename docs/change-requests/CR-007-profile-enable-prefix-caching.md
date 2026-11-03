# CR-007 (수락됨, 계약 3.2.0 반영, 이 영역 적용 완료): RuntimeProfile.engine에 enable_prefix_caching을 허용해 주세요 (헤드에게)

작성: 2026-11-03, 브랜치 wo-320. 코드와 프로파일은 바꾸지 않았다.

## 무엇이 부족한가
계약 3.2.0의 `RuntimeProfile.engine`은 `additionalProperties: false`이고 허용 키는 dtype, max_model_len, gpu_memory_utilization, max_num_seqs, max_loras, max_lora_rank, enforce_eager, quantization, guided_decoding_backend, structured_outputs_backend, disable_log_stats, tensor_parallel_size다. 이 영역은 vLLM의 접두사 캐시를 프로파일에 **명시**하려 한다(ADR 0034, 헤드 지시 wo-320 항목 3). 키가 없어 프로파일에 `enable_prefix_caching`을 쓰면 `load_profile`이 형식 위반으로 거부한다.

## 어떻게 바꿀지
`engine.properties`에 `"enable_prefix_caching": {"type": "boolean"}`을 더한다. 필수는 아니다(기존 프로파일은 그대로 유효). 코드는 프로파일 `engine`을 `vllm.LLM(**engine)`에 그대로 넘기므로 이 영역은 코드를 바꾸지 않는다.

## 이 영역이 계약 판을 받은 뒤 할 일
프로파일 4개(profiles/gpu-12g.json, gpu-dev-12g.json, gpu-shared-8b.json, rtx4090-24g.json)의 engine에 `"enable_prefix_caching": true`를 넣고, 키 존재를 보는 정적 시험을 더하고, ADR 0034를 채택으로 바꾼다.

## 추가 요청 (같은 계약 판 변경에 함께)
`ModelServingEnv`의 선언에 `HF_HUB_DISABLE_TELEMETRY`(문자열 `"1"`/`"0"` 등 Flag)를 더해 주세요. 이 영역의 이미지는 허깅페이스 원격 측정을 끄려고 `HF_HUB_DISABLE_TELEMETRY=1`을 ENV로 두고 있었다. 계약 3.2.0 선언은 `HF_` 접두사 이름 중 `HF_HOME`, `HF_HUB_OFFLINE`만 허용해서(그 밖은 시작 거부) 서버가 이미지 ENV 때문에 뜨지 못했다. 그래서 이번에는 ENV에서 뺐고 `DO_NOT_TRACK=1`에만 기댄다(허깅페이스 라이브러리가 이 표준 변수를 따르는지 이 환경에서 확인하지 못했다). 선언에 들어오면 이 영역이 ENV를 되돌린다.
