"""실제 HttpReleaseReader로 boundary-contracts의 참조 I4 서버에서 번들을 받아 적재하는 한 바퀴(읽기 전용 참조)."""
import copy
import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

BC = Path(__file__).resolve().parents[2] / "boundary-contracts"
sys.path.insert(0, str(BC))
try:
    from conformance.servers import ReferenceServer, adapter_digest, ref_of, sealed
    HAVE = True
except ImportError:
    HAVE = False

from helpers import GENERATOR_INPUT, PARSER_INPUT, SCHEMA_DIR
from test_server import call

import model_runtime as mr
from model_runtime import server


def make_release(rid, tamper=False):
    ex = json.loads((BC / "examples" / "ReleaseManifest.json").read_text())
    ex["release_id"] = rid
    ex["model_runtime_version"] = mr.RUNTIME_VERSION
    ex["runtime_contract_version"] = 1
    bank = {"version": 3, "questions": []}
    ex["question_set"] = ref_of("qs-main", "3", bank)
    cfgs = mr.fake_serving_configs()
    files = {r: {"adapter_config.json": json.dumps({"r": 8, "base_model_name_or_path": "x"}).encode(),
                 "adapter_model.safetensors": b"\0"} for r in cfgs}
    for r, c in cfgs.items():
        ex["adapters"][r] = {"adapter_hash": adapter_digest(files[r]), "serving_config": c,
                             "serving_config_hash": "sha256:" + "2" * 64 if tamper else mr.serving_config_hash(c)}
    return {"manifest": sealed(ex), "bank": bank, "adapters": files, "pass": True}


@unittest.skipUnless(HAVE, "boundary-contracts 참조 서버가 없다")
class E2E(unittest.TestCase):
    def test_put_loads_bundle_through_http_reader_and_serves(self):
        with ReferenceServer({"tok": "reader"}) as i4, TemporaryDirectory() as tmp:
            for rid, tamper in (("rel-ok", False), ("rel-bad", True)):
                i4.releases[rid] = make_release(rid, tamper); i4.release_order.append(rid)
            env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_PUT_WAIT_S": "10",
                   "MODEL_RUNTIME_RELEASE_URL": i4.url, "MODEL_RUNTIME_RELEASE_TOKEN": "tok", "MODEL_RUNTIME_BUNDLE_DIR": tmp}
            svc = server.build_service(env)
            srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(svc))
            threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.shutdown)
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            while not svc.ready:
                pass
            self.assertEqual(call(url, "/v1/releases/rel-ok", method="PUT")[1]["state"], "LOADED")
            self.assertEqual(call(url, "/v1/releases/rel-ok/parse", PARSER_INPUT)[0], 200)
            self.assertEqual(call(url, "/v1/releases/rel-ok/generate", GENERATOR_INPUT)[0], 200)
            s, b, _ = call(url, "/v1/releases/rel-bad", method="PUT")
            self.assertEqual((s, b["code"]), (412, "FAILED_PRECONDITION"))   # 설정 해시 불일치
            self.assertEqual(call(url, "/v1/releases/rel-ok/parse", PARSER_INPUT)[0], 200)
            s, b, _ = call(url, "/v1/releases/rel-none", method="PUT")        # I4에 없음
            self.assertEqual(s, 404)
