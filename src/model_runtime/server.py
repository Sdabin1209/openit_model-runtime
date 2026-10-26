"""model-runtime 추론 서버 (boundary-contracts/HTTP.md "I9 ModelRuntime 추론 서버", D62). 표준 라이브러리 HTTP.

경로: PUT /v1/releases/{id}(적재 신호, 멱등) · GET /v1/releases/{id}(상태) · POST /v1/releases/{id}/extract(LoRA-A, JSON)|respond(LoRA-B, text/plain) ·
GET /v1/info · GET /healthz · GET /readyz. 서버가 I4(HttpReleaseReader)에서 번들을 스스로 읽어 적재한다.
적재 실패는 이미 적재된 릴리스를 건드리지 않는다. 결과는 응답 헤더 X-Openit-Outcome(extract는 ok, respond는 ok|fallback)|Attempts|Protections.
extract가 재시도 후에도 ActionExtraction을 지키지 못하면 값을 지어내지 않고 503 UNAVAILABLE(details.reason=OUTPUT_INVALID, D75)을 낸다.
시스템 프롬프트는 적재한 릴리스 패키지(어댑터 디렉터리의 파일)의 것만 쓴다(D75).
엔진 호출은 직렬화하고, 대기가 길 것 같으면 큐에 쌓지 않고 503으로 거절한다. 발화·출력 원문은 로그에 남기지 않는다.

환경 변수(진입점만 읽는다): MODEL_RUNTIME_BACKEND(vllm|fake, 기본 vllm), MODEL_RUNTIME_SCHEMA_DIR(.../schema/schemas; 없으면 이미지에 넣은 /opt/survey-schema/schemas),
MODEL_RUNTIME_RELEASE_URL·MODEL_RUNTIME_RELEASE_TOKEN(I4 서버; 주소에 닿지 못하면 /readyz 503과 사유, /healthz는 200), MODEL_RUNTIME_READY_PROBE_TTL_S(도달성 확인 캐시, 기본 5), MODEL_RUNTIME_BUNDLE_DIR(기본 /var/lib/model-runtime/bundles; 쓸 수 없으면 /readyz 503과 이유),
MODEL_RUNTIME_FAKE_FLOW_FILE(fake 전용: 문항 ID 목록 JSON; LORA_A가 입력 question_id의 다음 문항으로 가는 NEXT를 낸다), MODEL_RUNTIME_FAKE_EXTRACTION(fake 전용: LORA_A 고정 출력 JSON, flow보다 우선),
MODEL_RUNTIME_PROFILE(프로파일 JSON, vllm은 base_model 필요), MODEL_RUNTIME_BASE_MODEL(프로파일의 base_model을 덮어씀; 어댑터의 기반 모델과 같아야 함),
HF_HOME·HF_HUB_OFFLINE(Hugging Face 표준 변수: 모델 캐시 위치와 오프라인; 이 서버가 읽지 않고 vLLM이 읽는다), MODEL_RUNTIME_TOKENS(역할=토큰,...; 있으면 /v1/*에 Bearer 필요),
MODEL_RUNTIME_QUEUE_WAIT_S(기본 3.0), MODEL_RUNTIME_PUT_WAIT_S(기본 3.0), MODEL_RUNTIME_MAX_RELEASES(기본 4),
MODEL_RUNTIME_HOST/PORT(기본 0.0.0.0:8080)."""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping

from boundary_contracts.errors import BoundaryError

from . import (RUNTIME_VERSION, SUPPORTED_RUNTIME_CONTRACT_VERSION, ContractVersionMismatch, ContractViolation,
               FakeBackend, ModelOutputInvalid, ModelRuntimeError, ServingConfigMismatch, SystemPromptMismatch,
               UnsupportedServingConfig, load, serving_config_hash)
from .backends import load_fake_flow, make_default_responder
from .errors import BackendNotAvailable
from .prompts import DEFAULT_TEMPLATE_VERSION, DEPRECATED_TEMPLATE_VERSIONS, SUPPORTED_TEMPLATE_VERSIONS
from .protection import PROTECTION_POLICIES
from .schema_guard import RELAX_POLICY_VERSION

log = logging.getLogger("model_runtime.server")
MAX_BODY = 4 * 1024 * 1024
DEFAULT_BUNDLE_DIR = "/var/lib/model-runtime/bundles"
EMBEDDED_SCHEMA_DIR = "/opt/survey-schema/schemas"   # 이미지에 스키마를 넣어 빌드한 경우(Dockerfile의 schema 컨텍스트)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
STATUS = {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409, "FAILED_PRECONDITION": 412,
          "UNSUPPORTED_VERSION": 422, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}


def to_boundary(e: Exception) -> BoundaryError:
    """적재 중 예외를 BoundaryError로. 원문 없이 종류만 담는다."""
    if isinstance(e, BoundaryError):
        return e
    if isinstance(e, (ServingConfigMismatch, UnsupportedServingConfig, ContractViolation)) and not isinstance(e, BackendNotAvailable):
        return BoundaryError("FAILED_PRECONDITION", f"{type(e).__name__}: {str(e)[:200]}")
    if isinstance(e, SystemPromptMismatch):
        return BoundaryError("INTEGRITY_ERROR", f"{e.role}의 시스템 프롬프트 파일이 없거나 해시가 다르다")
    if isinstance(e, ContractVersionMismatch):
        return BoundaryError("UNSUPPORTED_VERSION", "문진 스키마 버전을 지원하지 않는다")
    return BoundaryError("UNAVAILABLE", f"{type(e).__name__}")


class Release:
    def __init__(self):
        self.state, self.error, self.session, self.hashes, self.prompt_hashes = "LOADING", None, None, None, None
        self.done = threading.Event()


class Service:
    def __init__(self, *, schema_dir: Path, bundle_dir: Path, base_backend_factory: Callable[[], object],
                 reader_factory: Callable[[], object], backend_name: str, tokens: Mapping[str, str] | None = None,
                 queue_wait_s: float = 3.0, put_wait_s: float = 3.0, max_releases: int = 4, test_hooks: Mapping | None = None,
                 release_url: str | None = None, release_required: bool = False, probe_ttl_s: float = 5.0,
                 hf_home: str | None = None, runtime_dirs: list[str] | None = None):
        self.runtime_dirs = [Path(d) for d in (runtime_dirs or [])]   # HOME·캐시 디렉터리(환경 변수로 정해진 것만)
        self.hf_home = Path(hf_home) if hf_home else None   # vllm 백엔드일 때만 받는다(모델 내려받기 캐시)
        self.test_hooks = dict(test_hooks or {})
        self.release_url, self.release_required, self.probe_ttl_s = release_url, release_required, probe_ttl_s
        self._probe = (0.0, None)   # (시각, 사유 또는 None)
        self.schema_dir, self.bundle_dir = Path(schema_dir), Path(bundle_dir)
        self.reader_factory, self.backend_name = reader_factory, backend_name
        self.tokens = dict(tokens or {})
        self.queue_wait_s, self.put_wait_s, self.max_releases = queue_wait_s, put_wait_s, max_releases
        self.releases: dict[str, Release] = {}      # 삽입 순서 = 적재 순서
        self.rlock = threading.Lock()                # 릴리스 표
        self.engine_lock = threading.Lock()          # 엔진 직렬화
        self.pending = 0                             # 실행 중 + 대기 중
        self.avg_s = 0.5                             # 호출 시간 지수 이동 평균
        self.base = None
        self.ready, self.not_ready_reason = False, "백엔드를 올리는 중"
        self.bundle_error = self._check_bundle_dir() or self._check_hf_cache() or self._check_runtime_dirs()
        if self.bundle_error:
            log.error("%s", self.bundle_error)
            self.not_ready_reason = self.bundle_error
            return   # 백엔드를 올리지 않는다. /readyz는 503과 이유를 낸다
        threading.Thread(target=self._start_backend, args=(base_backend_factory,), daemon=True).start()

    def _check_bundle_dir(self) -> str | None:
        """번들 캐시를 만들고 쓸 수 있는지 시작할 때 확인한다. 볼륨을 처음 마운트해도 동작해야 한다."""
        try:
            self.bundle_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.bundle_dir):
                pass
        except OSError as e:
            return f"번들 디렉터리 {self.bundle_dir}에 쓸 수 없다({type(e).__name__}). 볼륨 소유자를 uid {os.getuid()}로 하거나 MODEL_RUNTIME_BUNDLE_DIR를 바꾼다"
        return None

    def _check_runtime_dirs(self) -> str | None:
        """HOME·XDG/vLLM/torch/triton 캐시 디렉터리를 mkdir -p로 보장한다(볼륨 마운트가 이미지의 디렉터리를 가린다). 못 쓰면 사유."""
        for d in self.runtime_dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryFile(dir=d):
                    pass
            except OSError:
                return f"runtime dir not writable: {d} (uid {os.getuid()})"
        return None

    def _check_hf_cache(self) -> str | None:
        """모델 캐시(HF_HOME)를 만들고 쓸 수 있는지 확인한다. 이미지가 준비한 경로가 아닌 곳에 볼륨을 마운트하면 root 소유라 실패한다."""
        if self.hf_home is None:
            return None
        try:
            self.hf_home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.hf_home):
                pass
        except OSError:
            return f"hf cache not writable: {self.hf_home} (uid {os.getuid()})"
        return None

    def readiness(self) -> tuple[bool, str | None]:
        """요청을 받을 수 있는가: 백엔드·번들 디렉터리·I4 서버 도달성. 사유에는 주소의 호스트:포트만 담는다(토큰·경로 없음).
        /healthz는 이것과 무관하게 프로세스가 살아 있으면 200이다."""
        if not self.ready:
            return False, self.not_ready_reason
        if self.release_required and not self.release_url:
            return False, "MODEL_RUNTIME_RELEASE_URL이 설정되지 않았다"
        if self.release_url:
            t, why = self._probe
            if time.time() - t >= self.probe_ttl_s:
                try:
                    with urllib.request.urlopen(self.release_url.rstrip("/") + "/healthz", timeout=2):
                        why = None
                except Exception as e:   # noqa: BLE001
                    u = urlsplit(self.release_url)
                    why = f"release 서버({u.hostname}:{u.port or ''})에 닿지 못한다({type(e).__name__})"
                self._probe = (time.time(), why)
            if why:
                return False, why
        return True, None

    def close(self):
        """종료 시 엔진을 내린다(시그널 처리에서 부른다). 내리지 않으면 EngineCore가 GPU 메모리를 쥔 채 남는다."""
        self.ready = False
        with self.rlock:
            self.releases.clear()
        close = getattr(self.base, "close", None)
        if close:
            close()

    def _start_backend(self, factory):
        try:
            self.base = factory()
            self.ready, self.not_ready_reason = True, None
        except Exception as e:   # noqa: BLE001
            self.not_ready_reason = type(e).__name__
            log.error("백엔드를 올리지 못함: %s", type(e).__name__)

    # --- 적재 ---
    def put(self, rid: str):
        """(상태 코드, 본문). 멱등: LOADED면 200, LOADING이면 대기 후 상태, FAILED·ABSENT면 새로 시도한다."""
        if not self.ready:
            return 503, BoundaryError("UNAVAILABLE", self.not_ready_reason or "서버가 아직 준비되지 않았다").to_record()
        if self.release_required and not self.release_url:
            return 503, BoundaryError("UNAVAILABLE", "MODEL_RUNTIME_RELEASE_URL이 설정되지 않았다").to_record()
        with self.rlock:
            r = self.releases.get(rid)
            if r is None or r.state == "FAILED":
                r = self.releases[rid] = Release()
                threading.Thread(target=self._load, args=(rid, r), daemon=True).start()
        r.done.wait(self.put_wait_s)
        if r.state == "LOADED":
            return 200, self.status_body(rid, r)
        if r.state == "LOADING":
            return 202, self.status_body(rid, r)
        return STATUS[r.error.code], r.error.to_record()

    def _load(self, rid: str, r: Release):
        try:
            bundle = self.reader_factory().load_bundle(rid, dest=self.bundle_dir)
            m = bundle.manifest
            if m["runtime_contract_version"] != SUPPORTED_RUNTIME_CONTRACT_VERSION:
                raise BoundaryError("UNSUPPORTED_VERSION", "runtime_contract_version을 지원하지 않는다")
            if m["model_runtime_version"].split(".")[:2] != RUNTIME_VERSION.split(".")[:2]:
                raise BoundaryError("UNSUPPORTED_VERSION", "model_runtime_version의 MAJOR.MINOR가 이 서버와 다르다")
            cfgs = {k: m["adapters"][k]["serving_config"] for k in ("LORA_A", "LORA_B")}
            hs = {k: m["adapters"][k]["serving_config_hash"] for k in cfgs}
            prompts = {k: m["adapters"][k]["system_prompt"] for k in cfgs}   # 패키지의 시스템 프롬프트(파일은 어댑터 디렉터리 안, D75)
            backend = self.base.bind(bundle.adapters) if hasattr(self.base, "bind") else self.base   # 어댑터 사전 검사 포함
            session = load(bundle.adapters, cfgs, schema_dir=self.schema_dir, expected_serving_config_hashes=hs,
                           system_prompts=prompts, backend=backend)
            with self.rlock:
                r.session, r.hashes, r.state = session, dict(session.serving_config_hashes), "LOADED"
                r.prompt_hashes = dict(session.system_prompt_hashes)
                loaded = [k for k, v in self.releases.items() if v.state == "LOADED"]
                for k in loaded[:-self.max_releases] if len(loaded) > self.max_releases else []:
                    del self.releases[k]
            log.info("적재 완료 release=%s", rid)
        except Exception as e:   # noqa: BLE001  실패는 이 릴리스에만 기록한다. 다른 릴리스는 그대로다
            r.error, r.state = to_boundary(e), "FAILED"
            log.warning("적재 실패 release=%s code=%s", rid, r.error.code)
        finally:
            r.done.set()

    def status_body(self, rid: str, r: Release | None):
        if r is None:
            return {"release_id": rid, "state": "ABSENT", "error": None}
        return {"release_id": rid, "state": r.state, "error": r.error.to_record() if r.error else None}

    def get(self, rid: str):
        with self.rlock:
            return self.status_body(rid, self.releases.get(rid))

    def info(self):
        with self.rlock:
            loaded = [k for k, v in self.releases.items() if v.state == "LOADED"]
            hashes = self.releases[loaded[-1]].hashes if loaded else {}
            prompt_hashes = self.releases[loaded[-1]].prompt_hashes if loaded else {}
        base = self.base
        expect = None
        if base is not None:   # 매니페스트의 serving_config가 맞춰야 하는 값(deploy·model-lab이 참고한다)
            expect = {"constraint": {"backend": base.name, "backend_version": base.version,
                                     "supported_features": sorted(base.supported_features),
                                     "relax_policy_version": RELAX_POLICY_VERSION},
                      "prompt_template_versions": sorted(SUPPORTED_TEMPLATE_VERSIONS),
                      "prompt_template_version_default": DEFAULT_TEMPLATE_VERSION,   # 새 매니페스트는 이 값을 쓴다(D77)
                      "deprecated_prompt_template_versions": sorted(DEPRECATED_TEMPLATE_VERSIONS),
                      "protection_policy_versions": sorted(PROTECTION_POLICIES),
                      "output_contract": SUPPORTED_RUNTIME_CONTRACT_VERSION}
        return {"service": "model-runtime", "runtime_version": RUNTIME_VERSION,
                "supported_contract_versions": [SUPPORTED_RUNTIME_CONTRACT_VERSION], "backend": self.backend_name,
                "loaded": loaded, "serving_config_hash": hashes,   # 해시는 가장 최근에 적재한 릴리스의 것
                "system_prompt_sha256": prompt_hashes,   # 가장 최근에 적재한 릴리스의 패키지 프롬프트 해시(D75)
                "serving_config_expectation": expect, "test_hooks": self.test_hooks}

    # --- 추론 ---
    def infer(self, rid: str, op: str, body):
        """op: extract | respond. (상태, 본문, 헤더). extract의 본문은 dict, respond의 본문은 문장(str). 대기가 길 것 같으면 503."""
        with self.rlock:
            r = self.releases.get(rid)
            if r is None or r.state != "LOADED":
                return 404, BoundaryError("NOT_FOUND", "적재되지 않은 릴리스").to_record(), {}
            if self.pending > 0 and (self.pending + 1) * self.avg_s > self.queue_wait_s:
                return 503, BoundaryError("UNAVAILABLE", "대기가 길어 거절한다").to_record(), {}
            self.pending += 1
        try:
            if not self.engine_lock.acquire(timeout=self.queue_wait_s):
                return 503, BoundaryError("UNAVAILABLE", "대기가 길어 거절한다").to_record(), {}
            try:
                t = time.time()
                out = getattr(r.session, op)(body)
                info = r.session.last_call_info
                self.avg_s = 0.8 * self.avg_s + 0.2 * (time.time() - t)
            finally:
                self.engine_lock.release()
            headers = {"X-Openit-Outcome": info.outcome, "X-Openit-Attempts": str(info.attempts)}
            if info.protections:
                headers["X-Openit-Protections"] = ",".join(f"{k}:{v}" for k, v in info.protections)
            return 200, (out.text if op == "respond" else out), headers
        except ContractViolation as e:
            return 400, BoundaryError("INVALID_RECORD", str(e)).to_record(), {}
        except ModelOutputInvalid as e:   # D75: 값을 지어내지 않는다. 호출자(survey-service)가 한 번 다시 보낸다
            return 503, BoundaryError("UNAVAILABLE", "모델 출력이 ActionExtraction 형식을 지키지 못했다",
                                      {"reason": "OUTPUT_INVALID", "attempts": e.attempts}).to_record(), {}
        except Exception as e:   # noqa: BLE001 (엔진 사망 등)
            log.error("추론 실패 %s", type(e).__name__)
            return 503, BoundaryError("UNAVAILABLE", "추론 실패").to_record(), {}
        finally:
            with self.rlock:
                self.pending -= 1


def make_handler(svc: Service):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            log.info("%s %s", self.command, re.sub(r"/releases/[^/]+", "/releases/{id}", self.path.split("?")[0]))

        def _send(self, status: int, body, headers: Mapping[str, str] | None = None):
            """body가 str이면 text/plain(respond의 문장 그대로), 아니면 JSON."""
            if isinstance(body, str):
                data, ctype = body.encode("utf-8"), "text/plain; charset=utf-8"
            else:
                data, ctype = json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _err(self, code: str, message: str):
            self._send(STATUS[code], BoundaryError(code, message).to_record())

        def _authorized(self) -> bool:
            if not svc.tokens:
                return True
            auth = self.headers.get("Authorization", "")
            return auth.startswith("Bearer ") and auth[7:] in svc.tokens.values()

        def _route(self, method: str):
            p = self.path.split("?")[0]
            if method == "GET" and p == "/healthz":
                return self._send(200, {"status": "ok"})
            if method == "GET" and p == "/readyz":
                ok, why = svc.readiness()
                return self._send(200, {"status": "ready"}) if ok else self._send(503, {"status": "not ready", "reason": why})
            if not self._authorized():
                return self._err("PERMISSION_DENIED", "토큰이 없거나 맞지 않다")
            if method == "GET" and p == "/v1/info":
                return self._send(200, svc.info())
            m = re.fullmatch(r"/v1/releases/([^/]+)(?:/(extract|respond))?", p)
            if not m:
                return self._err("NOT_FOUND", "경로가 없다")
            rid, op = m.groups()
            if not ID_RE.match(rid):
                return self._err("INVALID_RECORD", "release_id 형식 위반")
            if op is None and method == "PUT":
                self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
                return self._send(*svc.put(rid))
            if op is None and method == "GET":
                return self._send(200, svc.get(rid))
            if op and method == "POST":
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if n <= 0 or n > MAX_BODY:
                        return self._err("INVALID_RECORD", "본문 크기가 맞지 않다")
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    return self._err("INVALID_RECORD", "JSON이 아니다")
                return self._send(*svc.infer(rid, op, body))
            self._err("NOT_FOUND", "경로가 없다")

        def do_GET(self): self._route("GET")
        def do_PUT(self): self._route("PUT")
        def do_POST(self): self._route("POST")

    return H


def build_service(env: Mapping[str, str], *, reader_factory: Callable[[], object] | None = None) -> Service:
    backend_name = env.get("MODEL_RUNTIME_BACKEND", "vllm")
    schema_dir = env.get("MODEL_RUNTIME_SCHEMA_DIR") or (EMBEDDED_SCHEMA_DIR if Path(EMBEDDED_SCHEMA_DIR).is_dir() else None)
    if not schema_dir:
        raise SystemExit("MODEL_RUNTIME_SCHEMA_DIR가 필요하다(이미지에 스키마가 들어 있지 않다)")
    tokens = dict(kv.split("=", 1) for kv in env.get("MODEL_RUNTIME_TOKENS", "").split(",") if "=" in kv)
    if env.get("MODEL_RUNTIME_FAKE_RISK_PHRASE"):
        raise SystemExit("MODEL_RUNTIME_FAKE_RISK_PHRASE는 계약 2.0.0에서 없어졌다(v2 출력에 위험 신호 표현이 없다). 설정을 지운다")
    if backend_name == "fake":
        try:
            responder = make_default_responder(
                load_fake_flow(env["MODEL_RUNTIME_FAKE_FLOW_FILE"]) if env.get("MODEL_RUNTIME_FAKE_FLOW_FILE") else None,
                json.loads(env["MODEL_RUNTIME_FAKE_EXTRACTION"]) if env.get("MODEL_RUNTIME_FAKE_EXTRACTION") else None)
        except (OSError, ValueError) as e:
            raise SystemExit(f"fake 출력 설정이 틀렸다(MODEL_RUNTIME_FAKE_FLOW_FILE / MODEL_RUNTIME_FAKE_EXTRACTION): {type(e).__name__}")
        factory = lambda: FakeBackend(responder)
    elif backend_name == "vllm":
        def factory():
            from .vllm_backend import VLLMBackend, load_profile
            prof = load_profile(env["MODEL_RUNTIME_PROFILE"]) if env.get("MODEL_RUNTIME_PROFILE") else {}
            for k, v in prof.get("env", {}).items():   # 프로파일이 정한 엔진용 환경(예: VLLM_USE_FLASHINFER_SAMPLER)
                if not k.startswith(("_",)):
                    os.environ.setdefault(k, str(v))
            return VLLMBackend(None, profile=prof, base_model=env.get("MODEL_RUNTIME_BASE_MODEL") or None)
    else:
        raise SystemExit("MODEL_RUNTIME_BACKEND는 vllm 또는 fake")
    custom_reader = reader_factory
    if reader_factory is None:
        def reader_factory():
            from boundary_contracts.clients.releases import HttpReleaseReader
            return HttpReleaseReader(env["MODEL_RUNTIME_RELEASE_URL"], env.get("MODEL_RUNTIME_RELEASE_TOKEN", ""), timeout=120)
    offline = env.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes")
    hf_home = None if (backend_name != "vllm" or offline) else (env.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"))
    dirs = [env[k] for k in ("HOME", "XDG_CACHE_HOME", "VLLM_CACHE_ROOT", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR", "TORCH_HOME")
            if env.get(k)]   # 환경 변수가 정한 것만 보장한다(이미지는 ENV로 모두 정해 둔다)
    return Service(schema_dir=Path(schema_dir), hf_home=hf_home, runtime_dirs=sorted(set(dirs)), release_url=env.get("MODEL_RUNTIME_RELEASE_URL") or None,
                   release_required=custom_reader is None, probe_ttl_s=float(env.get("MODEL_RUNTIME_READY_PROBE_TTL_S", "5")),
                   bundle_dir=Path(env.get("MODEL_RUNTIME_BUNDLE_DIR", DEFAULT_BUNDLE_DIR)),
                   base_backend_factory=factory, reader_factory=reader_factory, backend_name=backend_name, tokens=tokens,
                   queue_wait_s=float(env.get("MODEL_RUNTIME_QUEUE_WAIT_S", "3.0")),
                   put_wait_s=float(env.get("MODEL_RUNTIME_PUT_WAIT_S", "3.0")),
                   max_releases=int(env.get("MODEL_RUNTIME_MAX_RELEASES", "4")))


def main(env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    svc = build_service(env)
    host, port = env.get("MODEL_RUNTIME_HOST", "0.0.0.0"), int(env.get("MODEL_RUNTIME_PORT", "8080"))
    srv = ThreadingHTTPServer((host, port), make_handler(svc))
    log.info("대기 %s:%s backend=%s", host, srv.server_address[1], svc.backend_name)

    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
        svc.close()


if __name__ == "__main__":
    main()
