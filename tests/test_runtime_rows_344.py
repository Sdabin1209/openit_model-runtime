"""계약 3.4.x RUNTIME 행 시험(출력하는 쪽 = model-runtime): 상태 코드 + 본문 + 필요한 헤더를 단언한다.

행: R1.8(영역 게이트웨이 출구, CONNECT 403 -> EGRESS_DENIED), R5.1(HealthStatus, not_ready), R3.19(요청 상한 -> 503 UNAVAILABLE BoundaryError),
R4.6(재시작 뒤 404, GET은 200 ABSENT), R6.3(비밀 없음), 라벨 io.openit.exposure(egress), HTTP.md I9 401(WWW-Authenticate: Bearer)·/healthz·/readyz·/metrics 인증 없음.
시험은 실제 시간을 재지 않는다: 시계는 가짜(ADR 0029), 출구는 loopback에 띄운 CONNECT 자리표시 서버다."""
import json
import socketserver
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.errors import BoundaryError
from helpers import A_INPUT, B_INPUT, assert_valid
from test_server import call, start

from model_runtime import server
from model_runtime.components.link_release_fetch import probe, reader_factory_from_env

ROOT = Path(__file__).resolve().parents[1]


class ConnectStub(socketserver.ThreadingTCPServer):
    """자기 영역 게이트웨이 자리표시: CONNECT 요청의 목적지를 기록하고 정한 상태로 답한다(403이면 EGRESS_DENIED 경로)."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, status):
        self.status, self.targets = status, []

        class H(socketserver.StreamRequestHandler):
            def handle(h):
                line = h.rfile.readline().decode("latin-1")
                while h.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                self.targets.append(line.split()[:2])
                h.wfile.write(f"HTTP/1.1 {self.status} X\r\nContent-Length: 0\r\n\r\n".encode())
        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


ENV = {"MODEL_RUNTIME_RELEASE_URL": "https://release-gate:8443", "MODEL_RUNTIME_RELEASE_TOKEN": "t"}


class EgressR18(unittest.TestCase):
    def stub(self, status):
        s = ConnectStub(status); self.addCleanup(s.server_close); self.addCleanup(s.shutdown); return s

    def test_release_bundle_read_goes_through_the_region_gateway_connect_tunnel(self):
        s = self.stub(403)
        reader = reader_factory_from_env({**ENV, "OPENIT_EGRESS_PROXY": s.url})()
        with TemporaryDirectory() as d, self.assertRaises(BoundaryError) as cm:
            reader.load_bundle("r1", dest=Path(d) / "x")
        self.assertEqual(s.targets, [["CONNECT", "release-gate:8443"]])   # 목적지는 서비스 이름이고 터널은 자기 게이트웨이를 지난다
        self.assertEqual((cm.exception.code, cm.exception.details.get("reason"), cm.exception.retryable), ("PERMISSION_DENIED", "EGRESS_DENIED", False))

    def test_reachability_probe_uses_the_same_exit_only(self):
        s = self.stub(403)
        why = probe("https://release-gate:8443", egress_proxy=s.url)
        self.assertEqual(s.targets, [["CONNECT", "release-gate:8443"]])
        self.assertIn("닿지 못한다", why); self.assertNotIn(s.url, why)

    def test_proxy_environment_variables_are_not_read(self):
        import os
        s = self.stub(403)
        old = os.environ.get("HTTPS_PROXY"); os.environ["HTTPS_PROXY"] = s.url
        try:
            probe("https://release-gate.invalid:8443")   # 출구가 없으면 직접 나간다(개발); 환경의 프록시로 새지 않는다
        finally:
            os.environ.pop("HTTPS_PROXY") if old is None else os.environ.__setitem__("HTTPS_PROXY", old)
        self.assertEqual(s.targets, [])

    def test_bad_egress_value_refuses_to_start(self):
        for bad in ("https://127.0.0.1:3128", "http://gateway:3128", "http://127.0.0.1"):
            with TemporaryDirectory() as t, self.assertRaises(SystemExit) as cm:
                start(t, OPENIT_EGRESS_PROXY=bad)
            self.assertIn("OPENIT_EGRESS_PROXY", str(cm.exception))


class HealthR51(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def test_bodies_follow_health_status_and_not_ready_is_spelled_not_ready(self):
        s = start(self._t.name, tokens="survey=SS"); self.addCleanup(s[0].shutdown)
        _, url, svc, _ = s
        for path in ("/healthz", "/readyz"):
            st, b, _ = call(url, path)
            self.assertEqual(st, 200); assert_valid("HealthStatus", b)
        svc.engine_error = "RuntimeError"
        for token, keys in ((None, {"status"}), ("SS", {"status", "reason"})):
            st, b, _ = call(url, "/readyz", token=token)
            self.assertEqual((st, b["status"], set(b)), (503, "not_ready", keys)); assert_valid("HealthStatus", b)

    def test_healthz_readyz_metrics_need_no_auth_even_with_a_wrong_token(self):
        s = start(self._t.name, tokens="survey=SS"); self.addCleanup(s[0].shutdown)
        url = s[1]
        for path in ("/healthz", "/readyz", "/metrics"):
            self.assertEqual(call(url, path, token="wrong")[0], 200, path)   # 토큰이 있어도 무시한다(R10.11): 401·403이 없다


class ErrorsAndRestart(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def test_401_has_www_authenticate_and_a_full_boundary_error(self):
        s = start(self._t.name, tokens="survey=SS"); self.addCleanup(s[0].shutdown)
        st, b, h = call(s[1], "/v1/releases/r1", method="GET")
        self.assertEqual((st, h.get("WWW-Authenticate")), (401, "Bearer")); assert_valid("BoundaryError", b)
        self.assertEqual((b["code"], b["retryable"]), ("PERMISSION_DENIED", False))

    def test_after_restart_nothing_is_loaded_extract_respond_404_and_get_is_200_absent(self):
        for round_ in range(2):   # 두 번째 라운드가 "재시작": 새 서버는 앞 서버의 적재를 모른다(R4.6)
            s = start(self._t.name); self.addCleanup(s[0].shutdown)
            url = s[1]
            for path, body in (("/v1/releases/r1/extract", A_INPUT), ("/v1/releases/r1/respond", B_INPUT)):
                st, b, _ = call(url, path, body)
                self.assertEqual((st, b["code"]), (404, "NOT_FOUND")); assert_valid("BoundaryError", b)
            st, b, _ = call(url, "/v1/releases/r1")
            self.assertEqual((st, b), (200, {"release_id": "r1", "state": "ABSENT", "error": None})); assert_valid("ReleaseLoadStatus", b)
            self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 200)
            self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)
            s[0].shutdown()

    def test_r319_over_capacity_is_503_unavailable_boundary_error(self):
        s = start(self._t.name, MODEL_RUNTIME_QUEUE_WAIT_S="1.0"); self.addCleanup(s[0].shutdown)
        _, url, svc, _ = s
        call(url, "/v1/releases/r1", method="PUT")
        svc.avg_s = 2.0; svc.pending = 1   # 대기가 상한을 넘을 것 같다: 큐에 쌓지 않는다
        for path, body in (("/v1/releases/r1/extract", A_INPUT), ("/v1/releases/r1/respond", B_INPUT)):
            st, b, _ = call(url, path, body)
            self.assertEqual((st, b["code"], b["retryable"]), (503, "UNAVAILABLE", True)); assert_valid("BoundaryError", b)

    def test_default_request_limits_are_the_chain_values(self):
        s = start(self._t.name); self.addCleanup(s[0].shutdown)
        self.assertEqual(s[2].deadlines, {"extract": 20.0, "respond": 25.0})   # CONVENTIONS 시간 상한 사슬


class NeighborRefusalIsNotCallerRefusal(unittest.TestCase):
    """I9 PUT 실패 목록은 412·422·503이다: release-gate의 거절(출구 거절 포함)이 호출자(survey-service)에게 401·403·404로 새지 않는다."""
    def put_with(self, err):
        with TemporaryDirectory() as t:
            s = start(t, plan={"r1": err}); 
            try:
                call(s[1], "/v1/releases/r1", method="PUT")
                return call(s[1], "/v1/releases/r1", method="PUT")   # 첫 PUT이 202일 수 있어 한 번 더 부른다(멱등)
            finally:
                s[0].shutdown()

    def test_mapping(self):
        for code, want in (("PERMISSION_DENIED", 503), ("NOT_FOUND", 412), ("INVALID_RECORD", 503), ("FAILED_PRECONDITION", 412), ("UNAVAILABLE", 503)):
            st, b, _ = self.put_with(BoundaryError(code, "x", {"reason": "EGRESS_DENIED"} if code == "PERMISSION_DENIED" else None))
            self.assertEqual((code, st), (code, want)); assert_valid("BoundaryError", b)
            self.assertNotIn("EGRESS_DENIED", json.dumps(b))   # 이웃 사정의 세부는 밖으로 내지 않는다


class ExposureLabel(unittest.TestCase):
    def test_exposure_declares_the_only_outgoing_peer_release_gate(self):
        from boundary_contracts import validator
        decl = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
        self.assertEqual(list(validator("Exposure").iter_errors(decl)), [])
        self.assertEqual(decl["egress"], ["release-gate"])   # INTERFACES 한눈에: I4 읽기 목록에 model-runtime(번들). 그 밖의 상대를 부르지 않는다
        self.assertTrue(all(r["auth"]["kind"] == "denied" for r in decl["routes"]))
        from model_runtime.components.link_receive import MAX_BODY
        self.assertTrue(all(r["max_body_bytes"] == MAX_BODY for r in decl["routes"]))   # 게이트웨이의 413 상한 = 서버의 413 상한


if __name__ == "__main__":
    unittest.main()
