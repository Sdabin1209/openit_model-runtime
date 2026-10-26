import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import ReleaseBundle
from boundary_contracts.canonical import file_hash
from helpers import A_INPUT, B_INPUT, SCHEMA_DIR, assert_valid

import model_runtime as mr
from model_runtime import server


class StubReader:
    """HttpReleaseReader 대신. releases: id → manifest 수정 함수 또는 BoundaryError."""

    def __init__(self, tmp, plan=None):
        self.tmp, self.plan, self.calls = Path(tmp), plan or {}, 0

    def load_bundle(self, rid, *, dest):
        self.calls += 1
        step = self.plan.get(rid, lambda m: m)
        if isinstance(step, BoundaryError):
            raise step
        b = mr.FakeBackend()
        cfgs = mr.fake_serving_configs(b)
        ad = {}
        for r in cfgs:
            d = self.tmp / rid / r; d.mkdir(parents=True, exist_ok=True); ad[r] = d
            (d / "system_prompt.txt").write_text(f"{rid}/{r} 패키지 프롬프트", encoding="utf-8")
        m = {"release_id": rid, "runtime_contract_version": 1, "model_runtime_version": mr.RUNTIME_VERSION,
             "adapters": {r: {"adapter_hash": "sha256:" + "0" * 64, "serving_config": c, "serving_config_hash": mr.serving_config_hash(c),
                              "system_prompt": {"path": "system_prompt.txt", "sha256": file_hash(ad[r] / "system_prompt.txt")}}
                          for r, c in cfgs.items()}}
        return ReleaseBundle(step(m), {}, ad)


def start(tmp, plan=None, tokens=None, **kw):
    env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_PUT_WAIT_S": "5", "MODEL_RUNTIME_BUNDLE_DIR": str(Path(tmp) / "bundles")}
    env.update(kw)
    if tokens:
        env["MODEL_RUNTIME_TOKENS"] = tokens
    rd = StubReader(tmp, plan)
    svc = server.build_service(env, reader_factory=lambda: rd)
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(100):
        if svc.ready: break
        time.sleep(0.02)
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", svc, rd


def _body(r):
    raw = r.read()
    return raw.decode("utf-8") if r.headers.get_content_type() == "text/plain" else json.loads(raw)


def call(url, path, body=None, token=None, method=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, _body(r), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, _body(e), dict(e.headers)


class Server(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def srv(self, **kw):
        s = start(self._t.name, **kw); self.addCleanup(s[0].shutdown); return s

    def test_health_ready_info_before_any_release(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/healthz")[0], 200)
        self.assertEqual(call(url, "/readyz")[0], 200)
        _, info, _ = call(url, "/v1/info")
        self.assertEqual((info["supported_contract_versions"], info["loaded"], info["serving_config_hash"]), ([1], [], {}))

    def test_put_load_then_extract_respond(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "ABSENT")
        s, body, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, body["state"], body["error"]), (200, "LOADED", None))
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "LOADED")
        s, out, h = call(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((s, out, h["X-Openit-Outcome"], h["X-Openit-Attempts"]), (200, mr.backends.FAKE_EXTRACTION, "ok", "1"))
        self.assertTrue(h["Content-Type"].startswith("application/json"))
        self.assertNotIn("X-Openit-Protections", h)
        assert_valid("ActionExtraction", out)
        s, out, h = call(url, "/v1/releases/r1/respond", B_INPUT)   # 문장 그대로의 text/plain(JSON으로 감싸지 않는다)
        self.assertEqual((s, out, h["X-Openit-Outcome"]), (200, mr.backends.FAKE_RESPONSE_TEXT, "ok"))
        self.assertTrue(h["Content-Type"].startswith("text/plain"))
        info = call(url, "/v1/info")[1]
        self.assertEqual(info["loaded"], ["r1"]); self.assertEqual(set(info["serving_config_hash"]), {"LORA_A", "LORA_B"})
        self.assertEqual(set(info["system_prompt_sha256"]), {"LORA_A", "LORA_B"})

    def test_put_idempotent_does_not_reload(self):
        _, url, _, rd = self.srv()
        call(url, "/v1/releases/r1", method="PUT"); s, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, b["state"], rd.calls), (200, "LOADED", 1))

    def test_unloaded_release_is_404(self):
        _, url, _, _ = self.srv()
        s, b, _ = call(url, "/v1/releases/nope/extract", A_INPUT)
        self.assertEqual((s, b["code"]), (404, "NOT_FOUND"))

    def test_slow_load_returns_202_then_loaded(self):
        def slow(m):
            time.sleep(0.6); return m
        _, url, _, _ = self.srv(plan={"r1": slow}, MODEL_RUNTIME_PUT_WAIT_S="0.1")
        s, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((s, b["state"]), (202, "LOADING"))
        time.sleep(0.9)
        self.assertEqual(call(url, "/v1/releases/r1")[1]["state"], "LOADED")

    def test_failed_load_keeps_serving_release(self):
        def bad_hash(m):
            m["adapters"]["LORA_A"]["serving_config_hash"] = "sha256:" + "1" * 64; return m
        plan = {"bad": bad_hash,
                "ver": lambda m: dict(m, model_runtime_version="9.9.0"),
                "contract": lambda m: dict(m, runtime_contract_version=2),
                "badprompt": lambda m: m["adapters"]["LORA_A"].update(system_prompt={"path": "nope.txt", "sha256": "sha256:" + "3" * 64}) or m,
                "pre": BoundaryError("FAILED_PRECONDITION", "PASS 아님"),
                "integ": BoundaryError("INTEGRITY_ERROR", "해시"),
                "down": BoundaryError("UNAVAILABLE", "릴리스 서버 불가")}
        _, url, _, _ = self.srv(plan=plan)
        call(url, "/v1/releases/good", method="PUT")
        for rid, want in (("bad", 412), ("ver", 422), ("contract", 422), ("badprompt", 422), ("pre", 412), ("integ", 422), ("down", 503)):
            s, b, _ = call(url, f"/v1/releases/{rid}", method="PUT")
            self.assertEqual(s, want, rid)
            st = call(url, f"/v1/releases/{rid}")[1]
            self.assertEqual((st["state"], st["error"]["code"]), ("FAILED", b["code"]))
            self.assertEqual(call(url, "/v1/releases/good/extract", A_INPUT)[0], 200)   # 서빙 중인 릴리스는 그대로
        self.assertEqual(call(url, "/v1/info")[1]["loaded"], ["good"])

    def test_invalid_input_is_400(self):
        _, url, _, _ = self.srv()
        call(url, "/v1/releases/r1", method="PUT")
        s, out, _ = call(url, "/v1/releases/r1/extract", {"user_utterance": 1})
        self.assertEqual((s, out["code"], out["retryable"]), (400, "INVALID_RECORD", False))
        s, out, _ = call(url, "/v1/releases/r1/respond", A_INPUT)   # LoRA-A 입력을 respond에 보냄
        self.assertEqual((s, out["code"]), (400, "INVALID_RECORD"))
        s, out, _ = call(url, "/v1/releases/r1/extract", B_INPUT)
        self.assertEqual(s, 400)

    def test_extract_that_never_gets_valid_gives_503_output_invalid_without_inventing(self):
        _, url, svc, _ = self.srv()
        call(url, "/v1/releases/r1", method="PUT")
        svc.releases["r1"].session._backend = mr.FakeBackend.scripted({"LORA_A": ["아님"], "LORA_B": ["괜찮은 문장입니다."]})
        s, out, h = call(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((s, out["code"], out["retryable"], out["details"]), (503, "UNAVAILABLE", True, {"reason": "OUTPUT_INVALID", "attempts": 3}))
        self.assertNotIn("X-Openit-Outcome", h)
        self.assertEqual(call(url, "/v1/releases/r1/respond", B_INPUT)[0], 200)   # 다른 호출은 그대로

    def test_respond_that_never_gets_valid_gives_200_fallback_header(self):
        _, url, svc, _ = self.srv()
        call(url, "/v1/releases/r1", method="PUT")
        svc.releases["r1"].session._backend = mr.FakeBackend.scripted({"LORA_B": [""]})
        s, out, h = call(url, "/v1/releases/r1/respond", B_INPUT)
        self.assertEqual((s, h["X-Openit-Outcome"], h["X-Openit-Attempts"], h["X-Openit-Protections"]), (200, "fallback", "3", "G0:1,G5:1"))
        self.assertEqual(out, mr.fallback_text(B_INPUT))

    def test_system_prompt_of_the_loaded_release_is_what_the_model_sees(self):
        _, url, svc, _ = self.srv()
        call(url, "/v1/releases/r1", method="PUT"); call(url, "/v1/releases/r2", method="PUT")
        b1, b2 = mr.FakeBackend(), mr.FakeBackend()
        svc.releases["r1"].session._backend, svc.releases["r2"].session._backend = b1, b2
        call(url, "/v1/releases/r1/extract", A_INPUT); call(url, "/v1/releases/r2/respond", B_INPUT)
        self.assertIn("r1/LORA_A 패키지 프롬프트", b1.requests[0].prompt)   # LoRA 교체(D72)는 프롬프트까지 함께 바뀐다
        self.assertIn("r2/LORA_B 패키지 프롬프트", b2.requests[0].prompt)

    def test_tampered_or_missing_system_prompt_fails_the_load_with_integrity_error(self):
        def tamper(m):
            m["adapters"]["LORA_B"]["system_prompt"]["sha256"] = "sha256:" + "2" * 64; return m
        def missing(m):
            m["adapters"]["LORA_A"]["system_prompt"]["path"] = "nope.txt"; return m
        _, url, _, _ = self.srv(plan={"t": tamper, "m": missing})
        for rid in ("t", "m"):
            s, b, _ = call(url, f"/v1/releases/{rid}", method="PUT")
            self.assertEqual((s, b["code"]), (422, "INTEGRITY_ERROR"), rid)
            self.assertEqual(call(url, f"/v1/releases/{rid}/extract", A_INPUT)[0], 404)

    def test_token_required_except_health(self):
        _, url, _, _ = self.srv(tokens="svc=secret")
        self.assertEqual(call(url, "/healthz")[0], 200); self.assertEqual(call(url, "/readyz")[0], 200)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 403)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT", token="secret")[0], 200)

    def test_rejects_when_queue_would_exceed_wait(self):
        _, url, svc, _ = self.srv(MODEL_RUNTIME_QUEUE_WAIT_S="1.0")
        call(url, "/v1/releases/r1", method="PUT")
        svc.avg_s = 2.0; svc.pending = 1   # 이미 처리 중인 호출이 있고 평균 2초
        s, b, _ = call(url, "/v1/releases/r1/extract", A_INPUT)
        self.assertEqual((s, b["code"]), (503, "UNAVAILABLE"))
        svc.pending = 0
        self.assertEqual(call(url, "/v1/releases/r1/extract", A_INPUT)[0], 200)

    def test_old_paths_gone(self):
        _, url, _, _ = self.srv()
        self.assertEqual(call(url, "/v1/extract", A_INPUT)[0], 404)
        call(url, "/v1/releases/r1", method="PUT")
        for old in ("parse", "generate"):   # 1.x 경로는 2.0.0에서 없어졌다
            self.assertEqual(call(url, f"/v1/releases/r1/{old}", A_INPUT)[0], 404)


class Cr17(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def test_info_exposes_serving_config_expectation_for_fake(self):
        srv, url, _, _ = start(self._t.name); self.addCleanup(srv.shutdown)
        e = call(url, "/v1/info")[1]["serving_config_expectation"]
        b = mr.FakeBackend()
        self.assertEqual(e["constraint"], {"backend": "fake", "backend_version": "1", "supported_features": sorted(b.supported_features),
                                           "relax_policy_version": mr.RELAX_POLICY_VERSION})
        self.assertEqual(e["prompt_template_versions"], ["prompt-3", "prompt-4"])
        self.assertEqual(e["protection_policy_versions"], ["protect-2"])
        # 기대값으로 만든 설정은 PUT이 받아 준다(deploy가 이 값으로 매니페스트를 만들 수 있다)
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 200)

    def test_unwritable_bundle_dir_makes_readyz_503_with_reason(self):
        blocker = Path(self._t.name) / "file"; blocker.write_text("x")
        srv, url, svc, _ = start(self._t.name, MODEL_RUNTIME_BUNDLE_DIR=str(blocker / "sub")); self.addCleanup(srv.shutdown)
        s, body, _ = call(url, "/readyz")
        self.assertEqual(s, 503); self.assertIn("번들 디렉터리", body["reason"])
        s, body, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual(s, 503); self.assertIn("번들 디렉터리", body["message"])
        self.assertEqual(call(url, "/healthz")[0], 200)

    def test_missing_bundle_dir_is_created(self):
        d = Path(self._t.name) / "new" / "bundles"
        srv, url, _, _ = start(self._t.name, MODEL_RUNTIME_BUNDLE_DIR=str(d)); self.addCleanup(srv.shutdown)
        self.assertEqual(call(url, "/readyz")[0], 200); self.assertTrue(d.is_dir())

    def test_removed_fake_risk_phrase_switch_is_refused_loudly(self):
        """v2 출력에는 위험 신호 표현이 없어(계약 2.0.0) 스위치를 없앴다. 설정이 남아 있으면 조용히 무시하지 않고 시작을 거부한다."""
        for backend in ("fake", "vllm"):
            with self.assertRaises(SystemExit) as cm:
                server.build_service({"MODEL_RUNTIME_BACKEND": backend, "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR),
                                      "MODEL_RUNTIME_FAKE_RISK_PHRASE": "x"})
            self.assertIn("없어졌다", str(cm.exception))
        srv, url, _, _ = start(self._t.name); self.addCleanup(srv.shutdown)
        self.assertEqual(call(url, "/v1/info")[1]["test_hooks"], {})


class Location(unittest.TestCase):
    """서버는 주소·토큰·프로파일을 환경 변수로만 받는다. I4에 닿지 못해도 살아 있고, 닿으면 동작한다."""

    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)

    def _serve(self, env):
        svc = server.build_service({"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR),
                                    "MODEL_RUNTIME_BUNDLE_DIR": str(Path(self._t.name) / "b"), "MODEL_RUNTIME_READY_PROBE_TTL_S": "0", **env})
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
        threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.shutdown)
        for _ in range(100):
            if svc.ready: break
            time.sleep(0.02)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_unreachable_release_url_healthz_ok_readyz_503_then_recovers(self):
        import socket
        from http.server import BaseHTTPRequestHandler, HTTPServer
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # 아직 아무도 듣지 않는 포트
        url = self._serve({"MODEL_RUNTIME_RELEASE_URL": f"http://127.0.0.1:{port}", "MODEL_RUNTIME_PUT_WAIT_S": "5"})
        self.assertEqual(call(url, "/healthz")[0], 200)
        st, body, _ = call(url, "/readyz")
        self.assertEqual(st, 503); self.assertIn(f"127.0.0.1:{port}", body["reason"]); self.assertNotIn("/", body["reason"].replace("127.0.0.1", ""))
        st, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual((st, b["code"]), (503, "UNAVAILABLE"))   # 적재 시도도 UNAVAILABLE
        self.assertEqual(call(url, "/healthz")[0], 200)

        class H(BaseHTTPRequestHandler):   # I4가 뒤늦게 올라온 것처럼
            def log_message(self, *a): pass
            def do_GET(self):
                code, data = (200, b"{}") if self.path == "/healthz" else (404, json.dumps(BoundaryError("NOT_FOUND", "없음").to_record()).encode())
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
        up = HTTPServer(("127.0.0.1", port), H); threading.Thread(target=up.serve_forever, daemon=True).start(); self.addCleanup(up.shutdown)
        self.assertEqual(call(url, "/readyz")[0], 200)
        st, b, _ = call(url, "/v1/releases/r1", method="PUT")
        self.assertEqual(st, 404)   # 닿았고 I4가 그 릴리스를 모른다고 답한 것(전체 적재는 test_server_e2e.py)

    def test_missing_release_url_is_readyz_503_with_reason(self):
        url = self._serve({})
        self.assertEqual(call(url, "/healthz")[0], 200)
        st, body, _ = call(url, "/readyz")
        self.assertEqual(st, 503); self.assertIn("MODEL_RUNTIME_RELEASE_URL", body["reason"])
        self.assertEqual(call(url, "/v1/releases/r1", method="PUT")[0], 503)

    def test_no_environment_specific_defaults_in_source(self):
        src = "\n".join(p.read_text(encoding="utf-8") for p in (Path(server.__file__).parent).glob("*.py"))
        for bad in ("release-gate", "host.docker.internal", "survey-service:", "localhost"):
            self.assertNotIn(bad, src)


class HfCache(unittest.TestCase):
    def test_unwritable_hf_cache_gives_clear_readyz_reason(self):
        with TemporaryDirectory() as t:
            blocker = Path(t) / "file"; blocker.write_text("x")
            hf = str(blocker / "hf")
            svc = server.Service(schema_dir=SCHEMA_DIR, bundle_dir=Path(t) / "b", base_backend_factory=mr.FakeBackend,
                                 reader_factory=lambda: None, backend_name="vllm", hf_home=hf)
            ok, why = svc.readiness()
            self.assertFalse(ok)
            self.assertEqual(why, f"hf cache not writable: {hf} (uid {__import__('os').getuid()})")
            ok_svc = server.Service(schema_dir=SCHEMA_DIR, bundle_dir=Path(t) / "b2", base_backend_factory=mr.FakeBackend,
                                    reader_factory=lambda: None, backend_name="vllm", hf_home=str(Path(t) / "hf-ok"))
            for _ in range(100):
                if ok_svc.ready: break
                time.sleep(0.02)
            self.assertTrue(ok_svc.readiness()[0])

    def test_build_service_checks_hf_only_for_vllm_online(self):
        base = {"MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}
        with TemporaryDirectory() as t:
            bad = str(Path(t) / "f" / "hf"); (Path(t) / "f").write_text("x")
            env = dict(base, MODEL_RUNTIME_BUNDLE_DIR=str(Path(t) / "b"), HF_HOME=bad)
            self.assertIsNone(server.build_service(dict(env, MODEL_RUNTIME_BACKEND="fake")).hf_home)       # fake는 모델을 받지 않는다
            self.assertIsNone(server.build_service(dict(env, MODEL_RUNTIME_BACKEND="vllm", HF_HUB_OFFLINE="1")).hf_home)
            svc = server.build_service(dict(env, MODEL_RUNTIME_BACKEND="vllm"))
            self.assertIn("hf cache not writable", svc.not_ready_reason)


class RuntimeDirs(unittest.TestCase):
    def test_runtime_dirs_created_when_missing_and_reason_when_not_writable(self):
        with TemporaryDirectory() as t:
            mk = lambda **kw: server.build_service({"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR),
                                                    "MODEL_RUNTIME_BUNDLE_DIR": str(Path(t) / "b"), **kw})
            env = {"HOME": str(Path(t) / "home"), "XDG_CACHE_HOME": str(Path(t) / "cache"),
                   "VLLM_CACHE_ROOT": str(Path(t) / "cache" / "vllm"), "TRITON_CACHE_DIR": str(Path(t) / "cache" / "triton")}
            svc = mk(**env)
            for _ in range(100):
                if svc.ready: break
                time.sleep(0.02)
            self.assertTrue(svc.ready)
            for v in env.values():
                self.assertTrue(Path(v).is_dir())   # 빈 볼륨이 이미지의 디렉터리를 가려도 시작 때 만든다
            (Path(t) / "f").write_text("x")
            bad = mk(TRITON_CACHE_DIR=str(Path(t) / "f" / "triton"))
            self.assertEqual(bad.not_ready_reason, f"runtime dir not writable: {Path(t) / 'f' / 'triton'} (uid {__import__('os').getuid()})")
            self.assertFalse(bad.readiness()[0])

    def test_dockerfile_layer_order_and_defaults(self):
        df = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
        pip, apt, env, user = (df.index(x) for x in ("RUN pip install -r /tmp/requirements.lock", "apt-get install -y --no-install-recommends gcc libc6-dev",
                                                     "ENV CC=gcc", "\nUSER 10001"))
        self.assertLess(pip, apt); self.assertLess(apt, env); self.assertLess(env, user)   # apt는 무거운 pip 레이어 뒤, USER 앞
        # 커밋·버전마다 바뀌는 ARG/LABEL은 무거운 레이어(pip, apt)와 모든 COPY·RUN보다 뒤에 있어야 빌드 캐시가 재사용된다
        last_layer = max(df.rindex(x) for x in ("\nRUN ", "\nCOPY "))
        for x in ("ARG REVISION", "ARG BOUNDARY_VERSION", "ARG SURVEY_SCHEMA_VERSION", "ARG TURN_ENGINE_VERSION", "LABEL io.openit.service", "io.openit.model-runtime="):
            self.assertGreater(df.index(x), last_layer, x)
        self.assertLess(df.index("ARG VARIANT"), df.index("COPY requirements-"))   # 잠금 파일 선택에 쓰이므로 앞
        for k in ("VLLM_NO_USAGE_STATS=1", "DO_NOT_TRACK=1", "HF_HUB_DISABLE_TELEMETRY=1", "TRITON_CACHE_DIR=/var/lib/model-runtime/cache/triton",
                  "TORCHINDUCTOR_CACHE_DIR=", "VLLM_CACHE_ROOT=", "XDG_CACHE_HOME=", "TORCH_HOME=", "HOME=/var/lib/model-runtime/home"):
            self.assertIn(k, df)
        self.assertIn('"$VARIANT" = "gpu"', df)   # CPU 변형에는 컴파일러를 넣지 않는다
