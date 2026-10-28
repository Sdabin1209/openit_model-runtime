"""시험용 가짜 서버(run_session --server-module). util이 0.82 초과면 vLLM의 메모리 부족 문구를 내고 '준비 안 됨'으로 남는다(실제 서버와 같은 모양), 이하면 /healthz·/readyz만 200."""
import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
util = json.load(open(os.environ["MODEL_RUNTIME_PROFILE"]))["engine"]["gpu_memory_utilization"]
mode = os.environ.get("STUB_FAIL", "memory")
if util > 0.82:
    if mode == "memory":
        print(f"ValueError: Free memory on device (5.1/11.5 GiB) on startup is less than desired GPU memory utilization ({util}, 10.3 GiB).", file=sys.stderr)
    else:
        print("ValueError: No available memory for the cache blocks. Try increasing `gpu_memory_utilization` when initializing the engine.", file=sys.stderr)
    logging.error("백엔드를 올리지 못함: ValueError")
    sys.stderr.flush()
    time.sleep(600)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path in ("/healthz", "/readyz") else 404); self.end_headers(); self.wfile.write(b"{}")
    do_PUT = do_GET

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", int(os.environ["MODEL_RUNTIME_PORT"])), H).serve_forever()
