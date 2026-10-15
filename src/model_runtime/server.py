"""model-runtime 추론 서버(표준 라이브러리 HTTP). 설정은 환경 변수로만 받는다(CONVENTIONS 7절: 읽는 곳은 진입점).

경로(제안, 변경 요청 003): POST /v1/parse(UtteranceParserInput → ParserOutput), POST /v1/generate(ResponseGeneratorInput →
GeneratorOutput), GET /healthz, GET /readyz, GET /v1/info. 응답 본문은 출력 계약 그대로이고, 보호 여부는 응답 헤더
X-Model-Runtime-Outcome·Attempts·Protections에 둔다. 요청은 한 번에 하나씩 처리한다(엔진이 스레드 안전하지 않다).
발화·출력 원문은 로그에 남기지 않는다.

환경 변수: MODEL_RUNTIME_BACKEND(vllm|fake, 기본 vllm), MODEL_RUNTIME_SCHEMA_DIR(필수, .../schema/schemas),
MODEL_RUNTIME_ADAPTER_LORA_A/_B(vllm), MODEL_RUNTIME_PROFILE(프로파일 JSON), MODEL_RUNTIME_SERVING_CONFIGS(JSON:
{"serving_configs": {...}, "expected_serving_config_hashes": {...}}; 없으면 백엔드에 맞춘 기본 설정, 개발용),
MODEL_RUNTIME_TOKENS(역할=토큰,...; 있으면 Bearer 필요), MODEL_RUNTIME_HOST/PORT(기본 0.0.0.0:8080)."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping

from boundary_contracts.errors import BoundaryError

from . import (RUNTIME_VERSION, ContractViolation, FakeBackend, FakeModelSession, ModelRuntimeError, load,
               fake_serving_configs, serving_config_hash)

log = logging.getLogger("model_runtime.server")
MAX_BODY = 4 * 1024 * 1024


class Service:
    def __init__(self, session, info: dict, tokens: Mapping[str, str] | None = None):
        self.session, self.info, self.tokens = session, info, dict(tokens or {})
        self.lock = threading.Lock()
        self.ready = session is not None
        self.not_ready_reason = None if self.ready else "세션이 없다"

    def call(self, op: str, body):
        with self.lock:
            out = getattr(self.session, op)(body)
            return out, self.session.last_call_info


def build_service(env: Mapping[str, str]) -> Service:
    backend_name = env.get("MODEL_RUNTIME_BACKEND", "vllm")
    schema_dir = env.get("MODEL_RUNTIME_SCHEMA_DIR")
    if not schema_dir:
        raise SystemExit("MODEL_RUNTIME_SCHEMA_DIR가 필요하다")
    tokens = dict(kv.split("=", 1) for kv in env.get("MODEL_RUNTIME_TOKENS", "").split(",") if "=" in kv)
    cfg_path = env.get("MODEL_RUNTIME_SERVING_CONFIGS")
    given = json.loads(Path(cfg_path).read_text(encoding="utf-8")) if cfg_path else None
    if backend_name == "fake":
        b = FakeBackend()
        cfgs = given["serving_configs"] if given else fake_serving_configs(b)
        hs = given["expected_serving_config_hashes"] if given else {r: serving_config_hash(c) for r, c in cfgs.items()}
        session = FakeModelSession(cfgs, schema_dir=Path(schema_dir), expected_serving_config_hashes=hs, backend=b)
        info = {"backend": "fake", "backend_version": b.version}
    elif backend_name == "vllm":
        from .vllm_backend import VLLMBackend, load_profile
        adapters = {r: Path(env[f"MODEL_RUNTIME_ADAPTER_{r}"]) for r in ("LORA_A", "LORA_B")}
        prof = load_profile(env["MODEL_RUNTIME_PROFILE"]) if env.get("MODEL_RUNTIME_PROFILE") else {}
        for k, v in prof.get("env", {}).items():   # 프로파일이 정한 엔진용 환경(예: VLLM_USE_FLASHINFER_SAMPLER)
            if not k.startswith("_"):
                os.environ.setdefault(k, str(v))
        b = VLLMBackend(adapters, profile=prof)
        cfgs = given["serving_configs"] if given else fake_serving_configs(b)
        hs = given["expected_serving_config_hashes"] if given else {r: serving_config_hash(c) for r, c in cfgs.items()}
        session = load(adapters, cfgs, schema_dir=Path(schema_dir), expected_serving_config_hashes=hs, backend=b)
        info = {"backend": "vllm", "backend_version": b.version, "base_model": b.base_model}
    else:
        raise SystemExit("MODEL_RUNTIME_BACKEND는 vllm 또는 fake")
    info.update(runtime_version=RUNTIME_VERSION, serving_config_hashes=dict(session.serving_config_hashes),
                serving_configs_source="manifest" if given else "default(개발용)")
    return Service(session, info, tokens)


def make_handler(svc: Service):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):   # 경로와 상태만. 본문은 남기지 않는다
            log.info("%s %s", self.command, self.path.split("?")[0])

        def _send(self, status: int, body, headers: Mapping[str, str] | None = None):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _err(self, status: int, code: str, message: str):
            self._send(status, BoundaryError(code, message).to_record())

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/healthz":
                return self._send(200, {"status": "ok"})
            if p == "/readyz":
                return self._send(200, {"status": "ready"}) if svc.ready else self._send(
                    503, {"status": "not ready", "reason": svc.not_ready_reason})
            if p == "/v1/info":
                return self._send(200, svc.info)
            self._err(404, "NOT_FOUND", "경로가 없다")

        def do_POST(self):
            op = {"/v1/parse": "parse", "/v1/generate": "generate"}.get(self.path.split("?")[0])
            if op is None:
                return self._err(404, "NOT_FOUND", "경로가 없다")
            if svc.tokens:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth[7:] not in svc.tokens.values():
                    return self._err(403, "PERMISSION_DENIED", "토큰이 없거나 맞지 않다")
            if not svc.ready:
                return self._err(503, "UNAVAILABLE", "준비되지 않았다")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    return self._err(400, "INVALID_RECORD", "본문 크기가 맞지 않다")
                body = json.loads(self.rfile.read(n))
            except ValueError:
                return self._err(400, "INVALID_RECORD", "JSON이 아니다")
            try:
                out, info = svc.call(op, body)
            except ContractViolation as e:
                return self._send(400, BoundaryError("INVALID_RECORD", str(e)).to_record())
            except ModelRuntimeError as e:
                return self._err(503, "UNAVAILABLE", type(e).__name__)
            except Exception as e:   # noqa: BLE001 (엔진 사망 등)
                log.error("추론 실패 %s", type(e).__name__)
                return self._err(503, "UNAVAILABLE", "추론 실패")
            self._send(200, out, {"X-Model-Runtime-Outcome": info.outcome, "X-Model-Runtime-Attempts": str(info.attempts),
                                  "X-Model-Runtime-Protections": ",".join(f"{k}:{v}" for k, v in info.protections)})

    return H


def main(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    svc = build_service(env)
    host, port = env.get("MODEL_RUNTIME_HOST", "0.0.0.0"), int(env.get("MODEL_RUNTIME_PORT", "8080"))
    srv = ThreadingHTTPServer((host, port), make_handler(svc))
    log.info("대기 %s:%s backend=%s", host, srv.server_address[1], svc.info["backend"])
    srv.serve_forever()


if __name__ == "__main__":
    main()
