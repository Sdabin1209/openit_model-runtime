"""GPU 세션 키트의 I4 읽기 서버(scripts/gpu_session/release_server.py): 표준 WSGI 앱 + waitress(ADR 0022와 같은 방식, ADR 0023)."""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tarfile
import io
import urllib.request
from pathlib import Path

from helpers import HANG_GUARD_S, wait_until
from test_gpu_session import KIT, ROOT, run_prepare, setup_inputs

SERVER = KIT / "release_server.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_server", SERVER)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def prepared(tmp_path):
    setup_inputs(tmp_path)
    r = run_prepare(tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "out"


def start(root, tmp_path):
    pf = tmp_path / "port"
    p = subprocess.Popen([sys.executable, str(SERVER), str(root), "--port", "0", "--port-file", str(pf)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert wait_until(lambda: pf.exists() and pf.read_text().strip().isdigit()), p.stderr.read() if p.poll() is not None else "port file 없음"
    return p, f"http://127.0.0.1:{pf.read_text().strip()}"


def test_exposes_a_wsgi_app():   # 수정 전에는 BaseHTTPRequestHandler 서브클래스뿐이라 실패한다
    m = load_module()
    assert callable(m.make_app) and not hasattr(m, "ThreadingHTTPServer")
    seen = {}
    body = b"".join(m.make_app(Path("/nonexistent"))({"REQUEST_METHOD": "GET", "PATH_INFO": "/healthz"}, lambda s, h: seen.update(status=s)))
    assert seen["status"].startswith("200") and body == b"ok"


def test_serves_manifest_bundle_and_404(tmp_path):
    root = prepared(tmp_path)
    p, url = start(root, tmp_path)
    try:
        assert urllib.request.urlopen(url + "/healthz").read() == b"ok"
        m = json.loads(urllib.request.urlopen(url + "/v1/releases/gpu-session").read())
        assert m["release_id"] == "gpu-session"
        data = urllib.request.urlopen(url + "/v1/releases/gpu-session/bundle").read()
        names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
        assert "manifest.json" in names and any(n.startswith("adapters/LORA_A/") for n in names)
        for bad in ("/v1/releases/nope", "/v1/releases/..%2f", "/x"):
            try:
                urllib.request.urlopen(url + bad); assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404 and json.loads(e.read())["code"] == "NOT_FOUND"
    finally:
        p.kill(); p.wait()


def test_contract_reader_loads_the_bundle_from_it(tmp_path):
    from boundary_contracts.clients.releases import HttpReleaseReader
    root = prepared(tmp_path)
    p, url = start(root, tmp_path)
    try:
        b = HttpReleaseReader(url, "", timeout=HANG_GUARD_S).load_bundle("gpu-session", dest=tmp_path / "dest")   # timeout은 매달림 감시
        assert b.manifest["release_id"] == "gpu-session" and set(b.adapters) == {"LORA_A", "LORA_B"}
    finally:
        p.kill(); p.wait()


def test_waitress_settings_and_terminate_handlers():
    """종료 신호 처리 시간·연결 시한은 waitress와 운영체제의 동작이라 시험하지 않는다. 이 스크립트의 책임은 (1) waitress에 올바른 값을 넘기는가 (2) SIGTERM·SIGINT에 종료 코드 0으로 끝나는 처리기를 거는가다."""
    m = load_module()
    seen, handlers, closed = {}, {}, []

    class Srv:
        effective_port = 1
        def run(self):
            handlers[m.signal.SIGTERM](m.signal.SIGTERM, None)   # 종료 신호를 받은 것처럼 처리기를 직접 부른다
        def close(self): closed.append(True)

    orig = (m.waitress.create_server, m.signal.signal, sys.argv)
    m.waitress.create_server = lambda app, **k: seen.update(k) or Srv()
    m.signal.signal = lambda sig, h: handlers.__setitem__(sig, h)
    sys.argv = ["release_server", "/nonexistent"]
    try:
        try:
            m.main()
            raise AssertionError("SystemExit(0)이어야 한다")
        except SystemExit as e:
            assert e.code == 0
    finally:
        m.waitress.create_server, m.signal.signal, sys.argv = orig
    assert set(handlers) == {m.signal.SIGTERM, m.signal.SIGINT} and closed == [True]
    assert (seen["threads"], seen["connection_limit"], seen["channel_timeout"], seen["cleanup_interval"]) == (4, 8, 120, 1)   # ADR 0023 값
