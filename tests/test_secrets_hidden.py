"""비밀(D84): NAME_FILE을 읽고, 값이 로그·/readyz·/v1/info·오류 응답·예외 메시지에 나오지 않는다."""
import json
import logging
import os
import tempfile
import unittest
import urllib.error
import urllib.request

from helpers import SCHEMA_DIR
from test_server import call, start

SECRET = "sUp3r-s3cret-VALUE-9f2c"


class SecretsHidden(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        self.f = os.path.join(self.t.name, "tokens"); open(self.f, "w").write(f"svc={SECRET}\n")

    def _serve(self, **kw):
        srv, url, svc, _ = start(self.t.name, **kw); self.addCleanup(srv.shutdown)
        return url

    def test_file_token_authorizes_and_env_ignored_when_file_set(self):
        url = self._serve(MODEL_RUNTIME_TOKENS_FILE=self.f, MODEL_RUNTIME_TOKENS="svc=envvalue")
        self.assertEqual(call(url, "/v1/info", token=SECRET)[0], 200)
        self.assertNotEqual(call(url, "/v1/info", token="envvalue")[0], 200)   # 파일이 이긴다
        self.assertNotEqual(call(url, "/v1/info")[0], 200)

    def test_env_still_works(self):
        url = self._serve(MODEL_RUNTIME_TOKENS=f"svc={SECRET}")
        self.assertEqual(call(url, "/v1/info", token=SECRET)[0], 200)

    def test_value_absent_from_logs_and_responses(self):
        with self.assertLogs(level="DEBUG") as cm:
            logging.getLogger("model_runtime.server").info("probe")   # assertLogs가 비지 않게
            url = self._serve(MODEL_RUNTIME_TOKENS_FILE=self.f)
            bodies = [call(url, "/readyz"), call(url, "/healthz"), call(url, "/v1/info", token=SECRET),
                      call(url, "/v1/info", token="wrong"), call(url, "/v1/info"), call(url, "/v1/releases/r1", token="wrong")]
        blob = json.dumps([(s, b, dict(h)) for s, b, h in bodies], ensure_ascii=False) + "\n".join(cm.output)
        self.assertNotIn(SECRET, blob)
        self.assertTrue(any("출처" in m and "file" in m for m in cm.output))   # 출처만 로그에 있다

    def test_unreadable_file_fails_without_value(self):
        from model_runtime import server
        env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_TOKENS_FILE": os.path.join(self.t.name, "missing"),
               "MODEL_RUNTIME_TOKENS": f"svc={SECRET}"}
        with self.assertRaises(SystemExit) as cm:
            server.build_service(env, reader_factory=lambda: None)
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertIn("MODEL_RUNTIME_TOKENS_FILE", str(cm.exception))

    def test_release_token_read_from_file(self):
        from model_runtime import server
        seen = {}
        import boundary_contracts.clients.releases as rel
        orig = rel.HttpReleaseReader
        rel.HttpReleaseReader = lambda url, token, timeout: seen.setdefault("t", token)
        self.addCleanup(setattr, rel, "HttpReleaseReader", orig)
        p = os.path.join(self.t.name, "rt"); open(p, "w").write(SECRET + "\n")
        env = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR), "MODEL_RUNTIME_RELEASE_URL": "http://127.0.0.1:9", "MODEL_RUNTIME_RELEASE_TOKEN_FILE": p,
               "MODEL_RUNTIME_BUNDLE_DIR": self.t.name}
        svc = server.build_service(env)
        self.addCleanup(svc.close)
        svc.reader_factory()
        self.assertEqual(seen["t"], SECRET)


if __name__ == "__main__":
    unittest.main()
