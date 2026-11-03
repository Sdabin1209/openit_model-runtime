# ADR 0033: 소요 시간 지표를 prometheus_client로 기록하고 GET /metrics로 낸다

## 상태
채택(2026-11-03, 계약 3.2.0 METRICS.md '운영 소요 시간 지표' U71·D125). 버킷 값은 **측정 전의 값**이다.

## 맥락
D125는 환자 턴 2초를 목표(SLO)로, 실패 판정 상한(extract 20초·respond 25초, ADR 0031)을 따로 둔다. 목표 달성 여부는 지표로 재야 한다. 계약은 이 영역이 낼 지표의 이름과 라벨을 정했다: `openit_model_engine_duration_seconds`(op, outcome, attempts), `openit_model_queue_wait_seconds`(op), `openit_next_item_overridden_total`(op). 노출 방식과 수집은 deploy 소관이다.

## 결정
- 새 컴포넌트 `components/metrics_record.py`(입력 MetricsEvent, 출력 MetricsRecorded)가 사건을 기록하고 Prometheus 텍스트를 낸다. `release_registry`는 사건(dict)을 주입된 `observe`에 넘길 뿐 기록 방법을 모른다. 연결은 `wiring/service.py`.
- 서버 쪽 호출 하나(대기+추론+재시도)의 소요를 `engine_duration`에, 엔진 락 대기를 `queue_wait`에, `extract` 결과에 `NEXT_ITEM_FILLED` 보호가 있으면(서버가 모델의 next_item을 바꿈) `next_item_overridden`에 기록한다.
- outcome 값: `ok`, `fallback`(respond 대체 문장), `invalid`(OUTPUT_INVALID), `timeout`(ENGINE_TIMEOUT), `unavailable`(엔진 예외). 엔진을 부르지 않은 요청(404·400·큐 거절)은 `engine_duration`에 넣지 않는다. 큐 대기는 거절된 대기도 센다(꼬리를 빼면 p95가 낮게 나온다). 재시도로 `NEXT_ITEM_FILLED`가 마지막 시도에만 남으므로 카운터는 최종 결과 기준이다.
- `GET /metrics`는 인증 없이 연다(수집기가 토큰을 갖지 않는다). 게이트웨이에는 `deploy-exposure.json`에서 `denied`로 선언한다. 내용은 닫힌 라벨 집합의 숫자뿐이고 발화·출력 원문이 없다(시험으로 확인).
- 서비스마다 `CollectorRegistry`를 따로 둔다(전역 기본 레지스트리를 쓰지 않는다): 한 프로세스에 서비스가 여럿 뜨는 시험에서 값이 섞이지 않는다.

## 왜
- prometheus_client: 계약이 이름 규칙으로 Prometheus 관례(`_seconds`, `_total`)를 따르라고 했고, 파이썬 공식 클라이언트이며 GPU 잠금 파일에 이미 0.26.0이 들어 있다(vLLM의 의존). CPU 잠금에도 같은 버전으로 고정했다.
- 히스토그램 버킷: 목표 중심. `engine_duration`은 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 20, 25초(2초 근처를 촘촘히, 상한 20·25초까지는 성기게). `queue_wait`은 0.005~3초(목표 p95 ≤ 0.3초, 대기 상한 3.0초, ADR 0005). **실제 모델 지연을 재지 않았다.** 첫 실측(GPU) 뒤 조정한다.

## 검토한 대안과 버리는 이유
- 직접 쓴 텍스트 출력: 히스토그램 누적 버킷·이스케이프를 다시 구현하게 된다. 기각.
- OpenTelemetry: 계약 METRICS.md가 Prometheus 이름 관례를 정했고 수집은 deploy의 Prometheus 배선이다. 이 규모에 과하다. 비교 측정은 하지 않았다.
- /metrics에 Bearer 인증: 수집기 설정이 늘고, 내부망 전용이며 내용이 숫자뿐이다. 외부 노출은 게이트웨이 선언으로 막는다.

## 결과
얻는 것: D125 목표를 지표로 잴 수 있다. 감수하는 것: 의존 하나 추가, 호출마다 히스토그램 기록 비용(측정 안 함, 락 대기 뒤 호출 한 번이 초 단위라 무시할 만하다고 추정). 인증 없는 경로가 하나 늘었다(내부망 전제).

## 되돌리는 조건
실측으로 버킷이 분포를 못 가르면 값을 바꾼다(이름·라벨은 계약이 정하므로 바꾸지 않는다). 수집이 내부망 밖이 되면 인증을 다시 검토한다.
