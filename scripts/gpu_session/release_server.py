#!/usr/bin/env python3
"""prepare_bundle.py가 만든 릴리스 디렉터리를 I4(HTTP.md ReleaseReader) 모양으로 내주는 최소 서버(WSGI 앱 + waitress, 루프백 전용, GPU 세션 키트. ADR 0023).

model-runtime 서버는 PUT /v1/releases/{id} 때 MODEL_RUNTIME_RELEASE_URL의 I4에서 tar 번들을 받아 해시를 검증하며 적재한다. 운영에서는 release-gate가 그 서버지만
이 측정에는 게이트가 없으므로 같은 모양의 읽기 전용 응답만 내는 이것을 쓴다. 인증은 하지 않는다(루프백 바인딩만 허용). boundary-contracts conformance 참조 서버는
설치 패키지에 들어 있지 않아 서버에 따로 올려야 하므로 쓰지 않는다.

사용: release_server.py ROOT_DIR [--port N] [--port-file F]   (--port 0이면 빈 포트, 고른 포트를 --port-file에 쓴다)
엔드포인트: GET /healthz, GET /v1/releases/{id} (manifest.json), GET /v1/releases/{id}/bundle (tar: manifest.json, question_bank.json, adapters/<역할>/…)"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tarfile
import threading
from pathlib import Path

import waitress   # 추론 서버와 같은 환경에 이미 있다(requirements-*.lock). 키트 PYTHONPATH 방식에서는 추론 서버도 waitress가 필요하다


def make_app(root: Path):
    """I4 읽기 서버의 WSGI 앱(PEP 3333). HTTP 구문·연결·timeout은 waitress가 한다."""

    def err(start_response, status: str, code: str, msg: str):
        b = json.dumps({"code": code, "message": msg}, ensure_ascii=False).encode()
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(b)))])
        return [b]

    def tar_chunks(d: Path):
        """번들을 스트리밍한다(어댑터 약 350MB를 메모리에 올리지 않는다). tarfile은 쓰기만 하므로 스레드가 파이프에 쓰고 이쪽이 읽는다."""
        r, w = os.pipe()

        def write():
            try:
                with os.fdopen(w, "wb") as f, tarfile.open(fileobj=f, mode="w|") as tf:
                    for p in sorted(d.rglob("*")):
                        if p.is_file():
                            tf.add(p, arcname=p.relative_to(d).as_posix(), recursive=False, filter=_plain)
            except OSError:   # 읽는 쪽이 끊으면 파이프가 깨진다. 종료한다
                pass
        threading.Thread(target=write, daemon=True).start()
        try:
            while True:
                chunk = os.read(r, 1 << 20)
                if not chunk:
                    return
                yield chunk
        finally:
            os.close(r)   # 클라이언트가 중간에 끊어도(close) 쓰는 스레드가 BrokenPipe로 끝난다

    def app(environ, start_response):
        if environ["REQUEST_METHOD"] != "GET":
            return err(start_response, "404 Not Found", "NOT_FOUND", "릴리스가 없다")
        path = environ.get("PATH_INFO", "")
        if path == "/healthz":   # 추론 서버의 /readyz가 도달성을 이 경로로 본다
            start_response("200 OK", [("Content-Length", "2")])
            return [b"ok"]
        m = re.fullmatch(r"/v1/releases/([A-Za-z0-9._-]+)(|/bundle)", path)
        d = root / m.group(1) if m else None
        if not m or ".." in m.group(1) or not (d and (d / "manifest.json").is_file()):
            return err(start_response, "404 Not Found", "NOT_FOUND", "릴리스가 없다")
        if not m.group(2):
            b = (d / "manifest.json").read_bytes()
            start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(b)))])
            return [b]
        start_response("200 OK", [("Content-Type", "application/x-tar")])   # 길이를 모르므로 waitress가 chunked로 보낸다(HTTP/1.1 클라이언트)
        return tar_chunks(d)
    return app


def _plain(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.uid = ti.gid = 0; ti.uname = ti.gname = ""; ti.mode = 0o644
    return ti


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file")
    a = ap.parse_args()
    # 값(ADR 0023): 클라이언트는 같은 호스트의 추론 서버 하나뿐이라 waitress 기본 스레드 4와 연결 상한 8이면 충분하다고 본 것이고 측정하지 않았다.
    # channel_timeout 120은 추론 서버의 번들 읽기 timeout 기본값(MODEL_RUNTIME_RELEASE_READ_TIMEOUT_S 120)과 맞췄다. cleanup_interval 1은 추론 서버(ADR 0022)와 같다.
    srv = waitress.create_server(make_app(Path(a.root)), host="127.0.0.1", port=a.port, threads=4, connection_limit=8, channel_timeout=120,
                                 cleanup_interval=1, ident="release-server")
    port = srv.effective_port
    if a.port_file:
        Path(a.port_file).write_text(str(port))
    print(f"release_server 127.0.0.1:{port} root={a.root}", flush=True)

    def stop(*_):   # 우아한 종료: 추론 서버와 같다(ADR 0022). run()이 SystemExit을 받으면 진행 중인 요청을 마치고 돌아온다
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        srv.run()
    finally:
        srv.close()


if __name__ == "__main__":
    main()
