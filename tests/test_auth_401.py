"""CONVENTIONS 6절(D106): 토큰이 없거나 틀리면 401 + WWW-Authenticate: Bearer, 권한 없음만 403. 오류 본문 code는 둘 다 PERMISSION_DENIED."""
import unittest
from tempfile import TemporaryDirectory

from helpers import A_INPUT
from test_server import call, start


class Auth401(unittest.TestCase):
    def setUp(self):
        self._t = TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        s = start(self._t.name, tokens="loader=LL,survey=SS,other=OO", MODEL_RUNTIME_PUT_ROLES="loader,survey")
        self.addCleanup(s[0].shutdown); self.url = s[1]

    def test_missing_or_wrong_token_is_401_with_www_authenticate(self):
        for path, body, method, token in [("/v1/info", None, None, None), ("/v1/info", None, None, "nope"),
                                          ("/v1/releases/r1", None, "PUT", None), ("/v1/releases/r1", None, None, "LL "),
                                          ("/v1/releases/r1/extract", A_INPUT, None, None), ("/v1/releases/r1/extract", A_INPUT, None, "nope")]:
            s, b, h = call(self.url, path, body, token=token, method=method)
            self.assertEqual((s, b["code"], h.get("WWW-Authenticate")), (401, "PERMISSION_DENIED", "Bearer"), (path, token))
            self.assertFalse(b["retryable"])

    def test_authenticated_but_not_allowed_is_403_without_www_authenticate(self):
        s, b, h = call(self.url, "/v1/releases/r1", method="PUT", token="OO")   # PUT_ROLES 밖
        self.assertEqual((s, b["code"], h.get("WWW-Authenticate")), (403, "PERMISSION_DENIED", None))
        s, b, h = call(self.url, "/v1/releases/r1/extract", A_INPUT, token="LL")   # 적재 전용 역할은 추론 불가
        self.assertEqual((s, b["code"], h.get("WWW-Authenticate")), (403, "PERMISSION_DENIED", None))

    def test_health_endpoints_need_no_token(self):
        self.assertEqual(call(self.url, "/healthz")[0], 200)
        self.assertEqual(call(self.url, "/readyz")[0], 200)


if __name__ == "__main__":
    unittest.main()
