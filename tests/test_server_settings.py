"""서버에 넘기는 설정 값과 종료 처리. 시간을 재거나 기다리지 않는다(U422).

왜: 느린 연결을 N초에 끊는가, 연결 상한을 넘는 연결을 어떻게 하는가, SIGTERM 때 진행 중 요청을 마치는가는 waitress와 운영체제의 동작이다(의존은 시험하지 않는다).
이 영역의 책임은 (1) waitress에 올바른 값을 넘기는가 (2) 종료 신호를 받았을 때 올바른 순서로 닫는가 (3) 진행 중 호출을 마감까지 기다리는가(가짜 시계)다."""
import math
import signal
import threading
import unittest
from tempfile import TemporaryDirectory

from fake_clock import FakeClock
from helpers import HANG_GUARD_S, SCHEMA_DIR, wait_until
from model_runtime import server
from model_runtime.components import link_receive

BASE = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1"}


def service(t, clock=None, **env):
    return server.build_service({**BASE, "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_BUNDLE_DIR": t, **env}, reader_factory=lambda: None, clock=clock or FakeClock())


class WaitressSettings(unittest.TestCase):
    def capture(self, svc, **kw):
        seen = {}
        orig = link_receive.waitress.create_server
        link_receive.waitress.create_server = lambda app, **k: seen.update(k) or object()
        try:
            link_receive.make_server(svc, "127.0.0.1", 0, **kw)
        finally:
            link_receive.waitress.create_server = orig
        return seen

    def test_values_passed_to_waitress(self):
        with TemporaryDirectory() as t:
            svc = service(t, MODEL_RUNTIME_CONN_TIMEOUT_S="1.2"); self.addCleanup(svc.close)
            k = self.capture(svc, max_connections=7)
        self.assertEqual((k["threads"], k["connection_limit"]), (7, 7))   # 스레드가 연결 수보다 적으면 /healthz가 엔진 호출 뒤에 줄 선다(ADR 0022)
        self.assertEqual(k["channel_timeout"], math.ceil(1.2))   # waitress는 정수 초만 받는다: 올림해서 넘긴다
        self.assertEqual(k["cleanup_interval"], 1)
        self.assertEqual(k["max_request_body_size"], link_receive.MAX_BODY)

    def test_env_reaches_the_service(self):
        with TemporaryDirectory() as t:
            svc = service(t, MODEL_RUNTIME_CONN_TIMEOUT_S="3", MODEL_RUNTIME_MAX_LOADING="1"); self.addCleanup(svc.close)
            self.assertEqual((svc.conn_timeout_s, svc.max_loading), (3.0, 1))
            default = service(t); self.addCleanup(default.close)
            self.assertEqual(default.conn_timeout_s, server.CONN_TIMEOUT_S)
            self.assertEqual(default.deadlines, {"extract": 3.0, "respond": 5.0})   # 계약 표 값(합 8.0s + 여유 1.0s = 턴 마감 9s, D119)은 설정 값으로 본다. 합을 시간으로 재지 않는다

    def test_deadlines_sum_fits_the_turn_deadline(self):
        d = server.DEFAULT_DEADLINES_S
        self.assertLessEqual(d["extract"] + d["respond"] + 1.0, 9.0)   # 계약 '시간 상한 사슬' 표(D119): 한 턴 = extract + respond + 서버 처리 여유 1.0s <= 턴 마감 9s


class Shutdown(unittest.TestCase):
    def test_signal_handlers_exit_zero_and_close_in_order(self):
        """main()은 SIGTERM·SIGINT에 종료 처리기를 걸고, 서버를 닫은 뒤 진행 중 호출을 기다리고 엔진을 내린다. 실제 신호는 보내지 않는다(처리기를 직접 부른다)."""
        order, handlers = [], {}
        outer = self

        class Srv:
            effective_port = 0
            def run(self):
                with outer.assertRaises(SystemExit) as cm:   # 등록된 처리기를 직접 부른다 = 종료 신호를 받은 것
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                outer.assertEqual(cm.exception.code, 0)
                order.append("run-interrupted")
                raise cm.exception
            def close(self): order.append("server.close")

        with TemporaryDirectory() as t:
            svc = service(t); self.addCleanup(svc.close)
            svc.drain = lambda grace: order.append(f"drain({grace})")
            svc.close = lambda: order.append("svc.close")
            orig = (server.make_server, server.build_service, server.signal.signal)
            server.make_server = lambda *a, **k: Srv()
            server.build_service = lambda env: svc
            server.signal.signal = lambda sig, h: handlers.__setitem__(sig, h)
            saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
            try:
                with self.assertRaises(SystemExit):
                    server.main({**BASE, "MODEL_RUNTIME_SHUTDOWN_GRACE_S": "7"})
            finally:
                server.make_server, server.build_service, server.signal.signal = orig
                for s, h in saved.items():
                    signal.signal(s, h)
        self.assertEqual(set(handlers), {signal.SIGTERM, signal.SIGINT})
        self.assertEqual(order, ["run-interrupted", "server.close", "drain(7.0)", "svc.close"])   # 새 연결을 닫고, 진행 중 호출을 기다린 뒤, 엔진을 내린다

    def test_drain_returns_when_inflight_calls_finish(self):
        clock = FakeClock()
        with TemporaryDirectory() as t:
            svc = service(t, clock); self.addCleanup(svc.close)
            svc.pending = 1
            out = {}
            th = threading.Thread(target=lambda: out.update(r=svc.drain(5.0)), daemon=True); th.start()
            self.assertTrue(wait_until(lambda: clock.blocked >= 1))   # 기다리는 중
            svc.pending = 0   # 진행 중 호출이 끝났다
            clock.advance(0.05)
            th.join(HANG_GUARD_S)
            self.assertEqual(out["r"], True)

    def test_drain_gives_up_at_the_grace_deadline(self):
        clock = FakeClock()
        with TemporaryDirectory() as t:
            svc = service(t, clock); self.addCleanup(svc.close)
            svc.pending = 1   # 끝나지 않는 호출
            self.assertEqual(clock.run_advancing(lambda: svc.drain(5.0), 5.0), False)   # 유예 시간이 지나면 기다리기를 그만둔다(가짜 시계로 5초를 흘림)
            svc.pending = 0


if __name__ == "__main__":
    unittest.main()
