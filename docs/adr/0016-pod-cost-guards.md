# 0016. 파드 비용 반복 제거: util 자동 재시도, 한 기동 다단계 부하(--plan), 키트 커밋 기록

## 상태
채택(2026-10-28). 0013, 0014를 보강한다.

## 맥락
임대 GPU는 시간당 과금이고 vLLM 서버 기동은 몇 분이 든다. (1) 기동이 'GPU 메모리를 요구량만큼 못 잡아서' 실패하면 사람이 util을 바꿔 파드에서 다시 띄웠다. (2) 부하 단계(동시 1, 4 ...)마다 서버를 다시 띄우면 기동 비용이 단계 수만큼 든다. (3) 결과가 어느 코드로 만든 것인지 결과 파일에서 알 수 없었다.

## 결정
1. `run_session.sh`: 기동 실패 로그가 **메모리 부족 종류**일 때만 util을 0.05씩 낮춰 최대 2번 다시 띄운다(0.90 -> 0.85 -> 0.80). 시도마다 `util_attempts.json`(util, 결과, 근거 한 줄)을 남기고, 실패한 시도의 서버 로그는 `logs/server.attemptN.log`로 보존하며, `measure.json`에 `util_start`, `util_final`, `util_attempts`를 합친다. 판별은 `util_retry.py`: "Free memory on device ... is less than desired GPU memory utilization", "CUDA out of memory"/"OutOfMemoryError"만 재시도 대상이다.
2. **KV 캐시 몫 부족**("No available memory for the cache blocks", "larger than the available KV cache memory")은 재시도하지 않는다. util을 낮추면 KV 몫이 더 줄어 더 나빠지고, vLLM 안내도 util을 올리거나 max_model_len을 줄이라고 한다(프로파일을 바꿀 일). 그 밖의 실패도 즉시 중단한다.
3. `measure.py --plan "1:20,4:20"`(`run_session.sh`, `pod_run.sh`가 받음): 서버 한 번 기동으로 단계(동시:건수)를 차례로 돌린다. 단계 사이와 적재 직후 GPU 메모리를 `stage_gpu`에 남긴다. `--concurrency/--n-a/--n-b`와 같이 쓰면 거부한다(두 곳에서 부하를 정하게 하지 않는다). 같은 동시 수를 두 번 쓰면 결과 키가 겹쳐 거부한다.
4. `make_kit.sh`는 만들 때 `git rev-parse HEAD`(+커밋 안 된 변경이 있으면 `-dirty`)를 `KIT_MANIFEST.txt`에 쓴다. 해시를 파일에 직접 적지 않으므로 순환이 없다. `pod_run.sh`, `pod_setup.sh`, `run_session.sh`가 이를 로그에 찍고, `run_session.sh`는 `measure.json`의 `kit_commit`과 `logs/KIT_MANIFEST.txt`에 남긴다.

## 왜
- 재시도 횟수 2와 단계 0.05는 측정이 아니라 지시된 값이다(0.90에서 0.80까지). 12GB급 GPU에서 다른 프로세스가 쓰는 몫이 수백 MiB~1GiB 단위라 0.05(약 0.6GiB) 단위가 한 번에 한 칸씩 내려가는 크기라고 보았다(추정, 측정 안 함).
- 판별 문구 중 "Free memory on device ... is less than desired GPU memory utilization"는 gpu-dev(RTX 4070 SUPER, vLLM 0.31.0)에서 `--util 0.935`로 실제 재현해 확인했다(`(10.79/11.99 GiB) on startup is less than desired GPU memory utilization (0.935, 11.21 GiB)`, 0.935 -> 0.89 재시도로 기동 성공, 2026-10-28). "CUDA out of memory"/"OutOfMemoryError"와 KV 캐시 문구는 vLLM 소스·보고된 로그 지식에 근거하며 실기에서 재현하지 못했다(`util_retry.py` 주석). 문구가 다르면 `other`로 분류돼 즉시 멈추므로 틀려도 임대 시간을 더 쓰는 쪽이 아니다.
- 서버 재기동은 몇 분 비용이라 부하 단계는 한 세션에서 이어 돌린다.

## 검토한 대안과 버리는 이유
- 모든 기동 실패를 재시도: 같은 실패를 반복해 비용만 쓴다. 기각.
- util을 높여 재시도(KV 부족일 때): 다른 프로세스가 메모리를 쓰는 환경에서 요구량만 늘린다. 기각, 사람이 프로파일을 바꾼다.
- 단계마다 새 세션: 기동 비용 반복. 기각.

## 결과
- 얻는 것: 메모리 부족 기동 실패의 자동 복구, 재기동 없는 다단계 부하, 결과와 코드 커밋의 연결.
- 감수하는 것: 낮은 util에서 KV 캐시가 줄어 처리량이 달라질 수 있다(결과에 util_final이 기록된다). 판별 문구 미확인.

## 되돌리는 조건
vLLM이 메모리 부족을 구분 가능한 종료 코드로 알리면 로그 문구 대신 그것을 쓴다. 실제 로그 문구가 위와 다르면 `util_retry.py`를 고친다.
