# ADR 0037: GPU 변형을 포함한 이미지가 자기 HEALTHCHECK 값을 가진다

## 상태
채택(2026-11-08, 헤드 지시 대장 #7).

## 맥락
deploy 의 `compose.llm.yaml` 이 model-serving(GPU) 이미지의 healthcheck(`start_period: 900s`, interval 15s, timeout 3s, retries 10)를 덮어썼다. 계약 CONVENTIONS 13절(3.4.17)은 영역 이미지가 자기 상태 확인(Dockerfile `HEALTHCHECK`)을 갖고 deploy 는 `healthcheck` 를 쓰지 않는다고 정한다. 이미지의 값은 `--start-period=120s`라 GPU 첫 기동(모델 내려받기와 적재)에는 짧았다.

## 결정
Dockerfile 의 HEALTHCHECK 를 `--interval=15s --timeout=5s --start-period=900s --retries=10`으로 한다(명령은 그대로 `/healthz`). 두 변형(cpu, gpu)이 같은 값을 쓴다.

## 왜
- 따른 표준: CONVENTIONS 13절(이미지가 상태 확인을 가진다), Docker HEALTHCHECK 의미(start-period 안의 실패는 재시도 횟수에 세지 않고, 한 번 성공하면 바로 healthy).
- 값의 출처: 900s·15s·10회는 deploy 가 덮어쓰던 잠정값을 그대로 옮겼다. **측정하지 않았다.** GPU 첫 기동의 내려받기(Qwen3 계열 수 GB 추정)와 적재 시간을 실제 GPU 호스트에서 재지 않았다. 측정 후 줄인다. timeout 만 3s에서 5s로 올렸다: 파이썬 프로세스 기동이 3초를 넘을 수 있는 느린 호스트에서 거짓 실패를 피하려는 것이며 이것도 측정하지 않았다.
- 변형·프로파일별로 값을 나누지 않은 이유: HEALTHCHECK 플래그는 ARG/ENV 치환을 지원하지 않는다(Dockerfile 참조). 가장 긴 쪽에 맞춰도 CPU 변형은 첫 성공에 바로 healthy 가 되므로 손해가 없다. 프로파일별 차이는 start-period 가 상한이라 흡수한다.

## 검토한 대안과 버리는 이유
- 변형마다 Dockerfile 단계 분기: 이미지 두 벌의 라벨·캐시 구조(레이어 순서 제약)를 건드린다. 기각.
- deploy 가 계속 덮어쓰기: 13절 위반. 기각.

## 결과
얻는 것: deploy 가 healthcheck 를 쓰지 않아도 GPU 첫 기동이 unhealthy 로 판정되지 않는다. 감수하는 것: 모델 적재가 실패해 멈춘 컨테이너가 최대 900s 동안 starting 으로 보인다(`/readyz`는 이 확인이 보지 않는다).

## 되돌리는 조건
GPU 호스트에서 첫 기동 시간을 재면 그 최악 값 위 여유로 start-period 를 다시 정한다(새 ADR 이 이 ADR 을 대체).
