"""metrics_record 컴포넌트: 운영 소요 시간 지표를 기록하고 Prometheus 텍스트로 낸다. MetricsEvent -> MetricsRecorded.

이름·라벨은 계약 METRICS.md '운영 소요 시간 지표'(3.2.0, U71)가 정한다: openit_model_engine_duration_seconds(op, outcome, attempts),
openit_model_queue_wait_seconds(op), openit_next_item_overridden_total(op). 라이브러리는 prometheus_client(ADR 0033).
기록 장부(Book)는 서비스마다 하나이고 wiring이 `book=`으로 넣는다. 전역 레지스트리를 쓰지 않는 이유: 한 프로세스에 서비스가 여럿 뜨는 시험에서 값이 섞인다.
발화·출력 원문은 다루지 않는다. 라벨은 닫힌 집합(MetricsEvent 스키마)이라 카디널리티가 늘지 않는다."""
from __future__ import annotations

from typing import Mapping

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

INPUT, OUTPUT = "MetricsEvent", "MetricsRecorded"
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"   # Prometheus 텍스트 노출 형식 0.0.4
# 버킷의 왜: 목표는 D125의 2초(턴 p95 ≤ 2.0, 모델 호출 합 ≤ 1.5)이고 실패 판정 상한은 extract 20초·respond 25초(ADR 0031)다.
# 그래서 2초 근처를 촘촘히(0.1~3), 상한까지는 성기게 둔다. 측정 전의 값이며 실측 뒤 조정한다(ADR 0033).
ENGINE_BUCKETS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0, 25.0)
# 큐 대기 목표 p95 ≤ 0.3초, 대기 상한 QUEUE_WAIT_S 기본 3.0초(ADR 0005)
QUEUE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0)


class Book:
    def __init__(self):
        self.registry = CollectorRegistry()   # 프로세스 전역 기본 레지스트리를 쓰지 않는다
        self.engine = Histogram("openit_model_engine_duration_seconds", "서버 쪽 호출 하나(대기+추론+재시도)의 소요", ["op", "outcome", "attempts"],
                                buckets=ENGINE_BUCKETS, registry=self.registry)
        self.queue = Histogram("openit_model_queue_wait_seconds", "엔진 큐 대기", ["op"], buckets=QUEUE_BUCKETS, registry=self.registry)
        self.overridden = Counter("openit_next_item_overridden", "모델이 낸 next_item과 서버가 채운 값이 다른 횟수", ["op"], registry=self.registry)   # 라이브러리가 _total을 붙인다


def run(req: Mapping, *, book: Book) -> dict:
    m = req["metric"]
    if m == "engine_duration":
        book.engine.labels(op=req["op"], outcome=req["outcome"], attempts=str(req["attempts"])).observe(req["seconds"])
    elif m == "queue_wait":
        book.queue.labels(op=req["op"]).observe(req["seconds"])
    else:
        book.overridden.labels(op=req["op"]).inc()
    return {"recorded": True}


def render(book: Book) -> bytes:
    """GET /metrics 본문(Prometheus 텍스트 형식)."""
    return generate_latest(book.registry)
