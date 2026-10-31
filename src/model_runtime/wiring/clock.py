"""실제 시계. 시간을 읽고 기다리는 모든 일의 창구다(ADR 0029).

컴포넌트(release_registry, retry_loop)는 `time`을 직접 읽거나 기다리지 않고 이 모양의 시계를 인자로 받는다. 시험은 같은 모양의 가짜 시계(tests/fake_clock.py)를 넣어
시간을 직접 흘린다. 왜 `monotonic()`만이 아니라 `wait`·`acquire`·`sleep`까지인가: Event.wait(timeout)·Lock.acquire(timeout)처럼 블로킹 대기는 전역 time을 바꿔도 움직이지 않는다."""
from __future__ import annotations

import threading
import time


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, event: threading.Event, timeout: float) -> bool:
        """event가 설정되면 True, timeout이 지나면 False."""
        return event.wait(timeout)

    def acquire(self, lock: threading.Lock, timeout: float) -> bool:
        return lock.acquire(timeout=timeout)
