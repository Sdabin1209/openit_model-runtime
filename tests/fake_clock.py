"""가짜 시계(ADR 0029). 시간은 시험이 advance()를 부를 때만 흐른다. 실제 시간을 재거나 기다려서 판정하는 곳이 없다.

모양은 model_runtime.wiring.clock.RealClock과 같다: monotonic, sleep, wait(event, timeout), acquire(lock, timeout).
블로킹 대기(wait·acquire·sleep)는 조건이 참이 되거나 가짜 시간이 마감에 닿을 때까지 돌아오지 않는다.
_TICK은 Event가 설정되는 것을 알아채려는 폴링 간격일 뿐이며 판정에 쓰이지 않는다(Event.set은 이 시계에 알리지 않는다)."""
import threading

HANG_GUARD_SECONDS = 300   # 매달림 감시: 정상이면 즉시 끝난다. 코드가 진짜 멈췄을 때만 걸리는 마감이며 합격·불합격의 기준이 아니다(정상 경로의 수천 배)
_TICK = threading.Event()  # 설정되지 않는 Event. 폴링 간격 용도


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self._now = start
        self.blocked = 0   # 지금 wait·acquire·sleep에서 기다리는 스레드 수(시험이 '기다림이 시작됐다'를 조건으로 알아보게)

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def _until(self, ready, timeout: float) -> bool:
        if ready():
            return True
        end = self._now + timeout
        self.blocked += 1
        try:
            while True:
                if ready():
                    return True
                if self._now >= end:
                    return False
                _TICK.wait(0.001)
        finally:
            self.blocked -= 1

    def sleep(self, seconds: float) -> None:
        self._until(lambda: False, seconds)

    def wait(self, event: threading.Event, timeout: float) -> bool:
        return self._until(event.is_set, timeout)

    def acquire(self, lock: threading.Lock, timeout: float) -> bool:
        return self._until(lambda: lock.acquire(blocking=False), timeout)

    def run_advancing(self, fn, seconds: float):
        """fn을 다른 스레드에서 부르고, fn이 이 시계에서 기다리기 시작하면 seconds만큼 시간을 흘려 마감을 일으킨 뒤 결과를 돌려준다."""
        out = {}
        th = threading.Thread(target=lambda: out.update(r=fn()), daemon=True); th.start()
        deadline_guard = threading.Event()
        while self.blocked < 1 and th.is_alive():
            deadline_guard.wait(0.001)
        self.advance(seconds)
        th.join(HANG_GUARD_SECONDS)   # 매달림 감시
        if th.is_alive():
            raise AssertionError("fn이 돌아오지 않았다")
        return out["r"]
