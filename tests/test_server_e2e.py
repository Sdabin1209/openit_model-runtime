"""실제 HttpReleaseReader로 boundary-contracts의 참조 I4 서버에서 번들을 받아 적재하고, 계약의 클라이언트(HttpModelRuntime)로
이 서버를 부르는 한 바퀴(적합성 시험). boundary-contracts는 형제 폴더를 읽기 전용으로 참조한다(설치된 boundary-contracts와 같은 내용이어야 한다. 아래 시험이 확인한다)."""
import copy
import json
import re
import sys
import threading
import unittest
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory

BC = Path(__file__).resolve().parents[2] / "boundary-contracts"
sys.path.append(str(BC))   # 뒤에 붙인다: boundary_contracts는 설치된 태그 패키지가 우선이고, conformance만 형제 폴더에서 온다
try:
    import conformance
    from conformance.servers import ReferenceServer, adapter_digest, ref_of, sealed
    HAVE = True
except ImportError:
    HAVE = False

from boundary_contracts import BoundaryError
from boundary_contracts.canonical import sha256_hash
from boundary_contracts.clients import HttpModelRuntime, is_output_invalid
from helpers import A_INPUT, B_INPUT, B_START, SCHEMA_DIR, assert_valid
from test_server import call

import model_runtime as mr
from model_runtime import server

PROMPTS = {"LORA_A": "시험용 LoRA-A 시스템 프롬프트".encode(), "LORA_B": "시험용 LoRA-B 시스템 프롬프트".encode()}


def make_release(rid, tamper=False):
    ex = json.loads((Path(conformance.__file__).resolve().parent / "examples" / "ReleaseManifest.json").read_text())   # 설치된 태그 패키지의 예제(형제 폴더 불필요)
    ex["release_id"] = rid
    ex["model_runtime_version"] = mr.RUNTIME_VERSION
    ex["runtime_contract_version"] = 1
    bank = {"version": 3, "questions": []}
    ex["question_set"] = ref_of("qs-main", "3", bank)
    cfgs = mr.fake_serving_configs()
    files = {r: {"adapter_config.json": json.dumps({"r": 8, "base_model_name_or_path": "x"}).encode(),
                 "adapter_model.safetensors": b"\0", "system_prompt.txt": PROMPTS[r]} for r in cfgs}
    for r, c in cfgs.items():
        ex["adapters"][r] = {"adapter_hash": adapter_digest(files[r]), "serving_config": c,
                             "serving_config_hash": "sha256:" + "2" * 64 if tamper else mr.serving_config_hash(c),
                             "system_prompt": {"path": "system_prompt.txt", "sha256": sha256_file(PROMPTS[r])}}
    return {"manifest": sealed(ex), "bank": bank, "adapters": files, "pass": True}


def sha256_file(data: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


@unittest.skipUnless(HAVE, "boundary-contracts 참조 서버가 없다")
class E2E(unittest.TestCase):
    def serve(self, i4, tmp, **extra):
        env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_PUT_WAIT_S": "10",
               "MODEL_RUNTIME_RELEASE_URL": i4.url, "MODEL_RUNTIME_RELEASE_TOKEN": "tok", "MODEL_RUNTIME_BUNDLE_DIR": tmp, **extra}
        svc = server.build_service(env)
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
        threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.shutdown)
        while not svc.ready:
            pass
        return svc, f"http://127.0.0.1:{srv.server_address[1]}"

    @unittest.skipUnless((BC / "pyproject.toml").exists(), "형제 boundary-contracts 작업 트리가 없다(새 머신에서는 정상, 이 시험은 형제 폴더와 설치본의 일치만 본다)")
    def test_reference_checkout_is_the_installed_tag(self):
        """적합성 시험이 읽는 형제 폴더와 설치된 패키지가 같은 계약 버전이다."""
        import boundary_contracts
        sibling = re.search(r'^version = "([^"]+)"', (BC / "pyproject.toml").read_text(), re.M).group(1)   # 버전을 여기에 또 적지 않는다
        self.assertEqual(metadata.version("boundary-contracts"), sibling)
        self.assertNotEqual(Path(boundary_contracts.__file__).resolve().parents[1], BC)   # 설치된 패키지를 쓰고 있다
        installed = Path(boundary_contracts.schema_dir())
        for f in sorted((BC / "schemas").glob("*.json")):   # 참조 서버 쪽 스키마와 설치된 스키마가 같은 내용이다
            self.assertEqual(f.read_bytes(), (installed / f.name).read_bytes(), f.name)

    def test_put_loads_bundle_through_http_reader_and_serves(self):
        with ReferenceServer({"tok": "reader"}) as i4, TemporaryDirectory() as tmp:
            for rid, tamper in (("rel-ok", False), ("rel-bad", True)):
                i4.releases[rid] = make_release(rid, tamper); i4.release_order.append(rid)
            svc, url = self.serve(i4, tmp)
            self.assertEqual(call(url, "/v1/releases/rel-ok", method="PUT")[1]["state"], "LOADED")
            self.assertEqual(call(url, "/v1/releases/rel-ok/extract", A_INPUT)[0], 200)
            self.assertEqual(call(url, "/v1/releases/rel-ok/respond", B_INPUT)[0], 200)
            s, b, _ = call(url, "/v1/releases/rel-bad", method="PUT")
            self.assertEqual((s, b["code"]), (412, "FAILED_PRECONDITION"))   # 설정 해시 불일치
            self.assertEqual(call(url, "/v1/releases/rel-ok/extract", A_INPUT)[0], 200)
            s, b, _ = call(url, "/v1/releases/rel-none", method="PUT")        # I4에 없음
            self.assertEqual(s, 404)
            info = call(url, "/v1/info")[1]
            self.assertEqual(info["system_prompt_sha256"], {r: sha256_file(p) for r, p in PROMPTS.items()})   # 패키지 프롬프트가 적재됐다

    def test_fake_flow_file_gives_next_item_over_http(self):
        """MODEL_RUNTIME_FAKE_FLOW_FILE: 서버 경로 전체(PUT 적재 후 extract)에서 입력 question_id의 다음 문항이 나온다."""
        with ReferenceServer({"tok": "reader"}) as i4, TemporaryDirectory() as tmp:
            i4.releases["rel-0001"] = make_release("rel-0001"); i4.release_order.append("rel-0001")
            flow = Path(tmp) / "flow.json"
            flow.write_text(json.dumps(["Q1.D01", "Q1.D02", "Q1.D03", "Q1.D04"]), encoding="utf-8")
            svc, url = self.serve(i4, tmp, MODEL_RUNTIME_FAKE_FLOW_FILE=str(flow))
            self.assertEqual(call(url, "/v1/releases/rel-0001", method="PUT")[1]["state"], "LOADED")
            s, out, _ = call(url, "/v1/releases/rel-0001/extract", A_INPUT)   # A_INPUT.question_id = Q1.D03
            self.assertEqual((s, out), (200, {"action": "NEXT", "next_item": "Q1.D04", "values": []}))
            assert_valid("ActionExtraction", out)

    def test_contract_client_conformance(self):
        """계약의 HttpModelRuntime으로 같은 시험(적재 멱등, 404, 입력 검증, 출력 형식, 헤더, OUTPUT_INVALID)을 이 서버에 돌린다."""
        with ReferenceServer({"tok": "reader"}) as i4, TemporaryDirectory() as tmp:
            i4.releases["rel-0001"] = make_release("rel-0001"); i4.release_order.append("rel-0001")
            svc, url = self.serve(i4, tmp)
            c = HttpModelRuntime(url, "")
            def code(fn, *a):
                with self.assertRaises(BoundaryError) as cm:
                    fn(*a)
                return cm.exception.code
            self.assertEqual(code(c.extract, "rel-0001", A_INPUT), "NOT_FOUND")   # 적재 전
            self.assertEqual(c.get_release_state("rel-0001")["state"], "ABSENT")
            self.assertEqual(c.load_release("rel-0001")["state"], "LOADED")
            self.assertEqual(c.load_release("rel-0001")["state"], "LOADED")        # 멱등
            self.assertIn("rel-0001", c.info()["loaded"])
            self.assertEqual(code(c.load_release, "../x"), "INVALID_RECORD")
            # extract: ActionExtraction (클라이언트가 엄격하게 검증), 헤더
            r = c.extract("rel-0001", A_INPUT)
            assert_valid("ActionExtraction", r.output)
            self.assertEqual((r.outcome, r.attempts, r.protections, r.is_fallback), ("ok", 1, {}, False))
            self.assertEqual(c.extract("rel-0001", A_INPUT), r)                     # 부작용 없음
            # 입력은 보내기 전에 클라이언트가, 서버도 검증한다
            self.assertEqual(code(c.extract, "rel-0001", {"question_id": "Q1.D01"}), "INVALID_RECORD")
            with self.assertRaises(BoundaryError) as cm:
                c._json("POST", "/v1/releases/rel-0001/extract", body={"question_id": "Q1.D01"})
            self.assertEqual(cm.exception.code, "INVALID_RECORD")
            # respond: 문장 그대로
            r = c.respond("rel-0001", B_START)
            self.assertEqual((r.output, r.outcome, r.is_fallback), (mr.backends.FAKE_RESPONSE_TEXT, "ok", False))
            # respond 대체 문장은 헤더로 알린다
            sess = svc.releases["rel-0001"].session
            sess._backend = mr.FakeBackend.scripted({"LORA_A": ["아님"], "LORA_B": [""]})
            r = c.respond("rel-0001", B_INPUT)
            self.assertEqual((r.output, r.outcome, r.attempts, r.protections, r.is_fallback),
                             (mr.fallback_text(B_INPUT), "fallback", 3, {"G0": 1, "G5": 1}, True))
            # extract가 끝내 형식을 못 지키면 503 OUTPUT_INVALID(D75), 값은 지어내지 않는다
            with self.assertRaises(BoundaryError) as cm:
                c.extract("rel-0001", A_INPUT)
            e = cm.exception
            self.assertEqual((e.code, e.retryable, e.details), ("UNAVAILABLE", True, {"reason": "OUTPUT_INVALID", "attempts": 3, "violations": ["json"]}))
            self.assertTrue(is_output_invalid(e))
            sess._backend = mr.FakeBackend()                                         # 다음 호출은 정상(상태가 남지 않는다)
            self.assertEqual(c.extract("rel-0001", A_INPUT).outcome, "ok")

    def test_token_is_enforced_with_contract_client(self):
        with ReferenceServer({"tok": "reader"}) as i4, TemporaryDirectory() as tmp:
            i4.releases["rel-0001"] = make_release("rel-0001"); i4.release_order.append("rel-0001")
            _, url = self.serve(i4, tmp, MODEL_RUNTIME_TOKENS="svc=secret")
            with self.assertRaises(BoundaryError) as cm:
                HttpModelRuntime(url, "wrong").load_release("rel-0001")
            self.assertEqual(cm.exception.code, "PERMISSION_DENIED")
            self.assertEqual(HttpModelRuntime(url, "secret").load_release("rel-0001")["state"], "LOADED")
