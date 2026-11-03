"""U99: model-runtime 의 바깥 호출은 TLS 만 쓴다(계약 3.4.1 require_tls_url), 검증 끄기·고정 주소 없음."""
import re
import unittest
from pathlib import Path

from model_runtime.components.link_release_fetch import probe

SRC = Path(__file__).resolve().parents[1] / "src" / "model_runtime"


class Tls(unittest.TestCase):
    def test_probe_rejects_non_loopback_plaintext(self):
        reason = probe("http://release-gate:8080")
        self.assertIsNotNone(reason)
        self.assertIn("ValueError", reason)

    def test_no_fixed_neighbor_address_or_verify_off(self):
        bad = re.compile(r"http://[a-z][a-z0-9-]+:[0-9]+|verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context")
        hits = [f"{p}:{i}" for p in SRC.rglob("*.py") for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if bad.search(l) and not l.lstrip().startswith("#")]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
