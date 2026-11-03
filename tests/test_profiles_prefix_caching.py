"""프로파일 4개가 vLLM prefix caching을 명시한다(ADR 0034). 기본값에 맡기지 않는다는 결정의 정적 점검이다. 효과는 측정하지 않았다."""
import json
import unittest
from pathlib import Path

PROFILES = sorted((Path(__file__).resolve().parents[1] / "profiles").glob("*.json"))


class PrefixCaching(unittest.TestCase):
    def test_four_profiles_exist_and_each_sets_it_true(self):
        self.assertEqual(len(PROFILES), 4)
        for p in PROFILES:
            self.assertIs(json.loads(p.read_text(encoding="utf-8"))["engine"].get("enable_prefix_caching"), True, p.name)


if __name__ == "__main__":
    unittest.main()
