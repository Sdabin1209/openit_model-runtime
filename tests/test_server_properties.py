"""추론 서버 구현(표준 HTTP 서버)이 보장해야 할 성질. 서버 프로세스를 `python -m` 진입점과 같은 main()으로 띄워서 본다.
구현이 바뀌어도(ADR 0022: 표준 라이브러리 서버 -> waitress) 같은 시험이 통과해야 한다: 느린 클라이언트가 다른 요청을 막지 않는다,
유휴 연결을 닫는다, 종료 신호에 진행 중 요청을 마친다, 엔진이 바쁜 동안에도 /healthz에 답한다."""
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import A_INPUT, SCHEMA_DIR, extract_body, wait_until

ROOT = Path(__file__).resolve().parents[1]

# 서버 진입점 main()을 그대로 쓰되, 릴리스 읽기와 모델만 시험용으로 바꾼다(extract가 DELAY초 걸린다).
LAUNCH = textwrap.dedent("""
    import json, os, sys, time
    sys.path[:0] = [os.environ["TESTS_DIR"]]
    from test_server import StubReader
    import model_runtime as mr
    from model_runtime.components import fake_backend
    from model_runtime import server
    delay = float(os.environ["DELAY"])
    def responder(req):
        if req.role == "LORA_A":
            if os.environ.get("ENTERED"):   # 엔진 안에 들어왔다는 표지. 시험은 고정 sleep 대신 이 파일을 기다린다
                open(os.environ["ENTERED"], "w").write("1")
            time.sleep(delay)
            return json.dumps(fake_backend.FAKE_EXTRACTION)
        return "문장입니다."
    real = server.build_service
    def build(env, reader_factory=None):
        svc = real(env, reader_factory=lambda: StubReader(os.environ["BUNDLES"]))
        svc.base_backend_factory = lambda: mr.FakeBackend(responder)
        return svc
    server.build_service = build
    server.FakeBackend = lambda _default=None: mr.FakeBackend(responder)   # build_service의 fake 공장이 이 응답기를 쓰게 한다(서비스 속성을 나중에 바꾸는 방식은 이미 시작된 백엔드 스레드에 닿지 않아 DELAY가 효과가 없었다)
    server.main()
""")


class Proc(unittest.TestCase):
    def launch(self, **extra):
        t = TemporaryDirectory(); self.addCleanup(t.cleanup)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "TESTS_DIR": str(ROOT / "tests"), "BUNDLES": t.name, "DELAY": "0",
               "MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR),
               "MODEL_RUNTIME_PORT": str(self.port), "MODEL_RUNTIME_BUNDLE_DIR": str(Path(t.name) / "b"), "MODEL_RUNTIME_SHUTDOWN_GRACE_S": "5",
               "MODEL_RUNTIME_EXTRACT_DEADLINE_S": "5", **extra}
        self.p = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: self.p.poll() is None and self.p.kill())
        self.assertTrue(wait_until(lambda: self.get("/healthz")[0] == 200, 60))
        self.assertIn(self.req("PUT", "/v1/releases/r1")[0], (200, 202))
        self.assertTrue(wait_until(lambda: json.loads(self.req("GET", "/v1/releases/r1")[1])["state"] == "LOADED", 60))

    def req(self, method, path, body=None, timeout=10):
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
            c.request(method, path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
            r = c.getresponse(); out = (r.status, r.read()); c.close()
            return out
        except OSError:
            return (0, b"")

    def get(self, path):
        return self.req("GET", path)


class Properties(Proc):
    def test_slow_clients_do_not_block_other_requests(self):
        self.launch()
        slow = []
        for _ in range(10):   # 헤더를 끝내지 않고 멈춘 연결 10개(slowloris 모양). 기본 연결 상한 32보다 적다
            s = socket.create_connection(("127.0.0.1", self.port)); s.sendall(b"POST /v1/releases/r1/extract HTTP/1.1\r\nHost: x\r\n")
            slow.append(s); self.addCleanup(s.close)
        self.assertEqual(self.get("/healthz")[0], 200)
        self.assertEqual(self.req("POST", "/v1/releases/r1/extract", extract_body(A_INPUT))[0], 200)   # 막혔다면 요청 시간 초과로 (0, b"")가 와서 위의 단언이 실패한다. 걸린 시간 자체는 판정이 아니다

    def test_idle_connection_is_closed_by_the_server(self):
        self.launch(MODEL_RUNTIME_CONN_TIMEOUT_S="1")
        s = socket.create_connection(("127.0.0.1", self.port)); self.addCleanup(s.close)
        s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n"); s.settimeout(60)   # 헤더를 끝내지 않고 멈춘다. 닫히기까지 시간은 부하에 달려 상한을 넉넉히 둔다
        self.assertEqual(s.recv(100), b"")

    def test_sigterm_finishes_the_inflight_request_and_exits_zero(self):
        with TemporaryDirectory() as d:
            entered = Path(d) / "entered"
            self.launch(DELAY="1.5", ENTERED=str(entered))
            out = {}
            import threading
            th = threading.Thread(target=lambda: out.update(r=self.req("POST", "/v1/releases/r1/extract", extract_body(A_INPUT), timeout=60))); th.start()
            self.assertTrue(wait_until(entered.exists, 30))   # extract가 엔진 안에 있다(고정 sleep 대신 표지 파일)
            self.p.send_signal(signal.SIGTERM)
            th.join(60)
            self.assertEqual(out["r"][0], 200)
            self.assertEqual(self.p.wait(timeout=60), 0)

    def test_healthz_answers_while_the_engine_is_busy(self):
        with TemporaryDirectory() as d:
            entered = Path(d) / "entered"
            self.launch(DELAY="6.0", ENTERED=str(entered), MODEL_RUNTIME_EXTRACT_DEADLINE_S="30")
            import threading
            th = threading.Thread(target=lambda: self.req("POST", "/v1/releases/r1/extract", extract_body(A_INPUT), timeout=60)); th.start()
            self.assertTrue(wait_until(entered.exists, 30))   # 엔진이 바쁘다
            for _ in range(3):   # 이어지는 추론 요청이 대기 상한에서 거절되는 동안에도
                threading.Thread(target=lambda: self.req("POST", "/v1/releases/r1/extract", extract_body(A_INPUT), timeout=60), daemon=True).start()
            self.assertEqual(self.get("/healthz")[0], 200)
            self.assertTrue(th.is_alive())   # /healthz가 돌아온 때에도 엔진 호출은 끝나지 않았다: 엔진 뒤에 줄 서지 않았다(걸린 시간이 아니라 순서로 판정)
            th.join(60)
