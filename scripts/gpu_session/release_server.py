#!/usr/bin/env python3
"""prepare_bundle.py가 만든 릴리스 디렉터리를 I4(HTTP.md ReleaseReader) 모양으로 내주는 최소 서버(표준 라이브러리만, 루프백 전용, GPU 세션 키트).

model-runtime 서버는 PUT /v1/releases/{id} 때 MODEL_RUNTIME_RELEASE_URL의 I4에서 tar 번들을 받아 해시를 검증하며 적재한다. 운영에서는 release-gate가 그 서버지만
이 측정에는 게이트가 없으므로 같은 모양의 읽기 전용 응답만 내는 이것을 쓴다. 인증은 하지 않는다(루프백 바인딩만 허용). boundary-contracts conformance 참조 서버는
설치 패키지에 들어 있지 않아 서버에 따로 올려야 하므로 쓰지 않는다.

사용: release_server.py ROOT_DIR [--port N] [--port-file F]   (--port 0이면 빈 포트, 고른 포트를 --port-file에 쓴다)
엔드포인트: GET /healthz, GET /v1/releases/{id} (manifest.json), GET /v1/releases/{id}/bundle (tar: manifest.json, question_bank.json, adapters/<역할>/…)"""
from __future__ import annotations

import argparse
import json
import re
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_handler(root: Path):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):   # 요청 로그를 남기지 않는다
            pass

        def _err(self, status: int, code: str, msg: str):
            b = json.dumps({"code": code, "message": msg}, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.split("?")[0] == "/healthz":   # 추론 서버의 /readyz가 도달성을 이 경로로 본다
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers()
                return self.wfile.write(b"ok")
            m = re.fullmatch(r"/v1/releases/([A-Za-z0-9._-]+)(|/bundle)", self.path.split("?")[0])
            d = root / m.group(1) if m else None
            if not m or ".." in m.group(1) or not (d and (d / "manifest.json").is_file()):
                return self._err(404, "NOT_FOUND", "릴리스가 없다")
            if not m.group(2):
                b = (d / "manifest.json").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers()
                return self.wfile.write(b)
            self.send_response(200); self.send_header("Content-Type", "application/x-tar"); self.send_header("Connection", "close"); self.end_headers()
            self.close_connection = True
            with tarfile.open(fileobj=self.wfile, mode="w|") as tf:   # 스트리밍(어댑터 약 350MB를 메모리에 올리지 않는다)
                for p in sorted(d.rglob("*")):
                    if p.is_file():
                        tf.add(p, arcname=p.relative_to(d).as_posix(), recursive=False, filter=_plain)
    return H


def _plain(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.uid = ti.gid = 0; ti.uname = ti.gname = ""; ti.mode = 0o644
    return ti


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.root)))
    port = srv.server_address[1]
    if a.port_file:
        Path(a.port_file).write_text(str(port))
    print(f"release_server 127.0.0.1:{port} root={a.root}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
