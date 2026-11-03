"""규칙 D93: respond 대체 문장을 만들 재료(known, ask, hold, next.text)가 하나도 없으면 문구 "죄송합니다. 다시 한 번 말씀해 주세요."를 그대로 쓴다
(사용자 확정, 열린 항목 G04). 문구는 한 글자도 바꾸지 않는다: 이 시험이 정본 문구를 한 번 더 적어 두어, 코드의 문구가 바뀌면 깨진다."""
import unittest

import model_runtime as mr
from helpers import B_END

D93_TEXT = "죄송합니다. 다시 한 번 말씀해 주세요."


class D93(unittest.TestCase):
    def test_policy_text_is_exactly_the_confirmed_sentence(self):
        for policy in mr.PROTECTION_POLICIES.values():
            self.assertEqual(policy.empty_fallback_text, D93_TEXT)

    def test_no_material_gives_the_sentence(self):
        self.assertEqual(mr.fallback_text(B_END | {"known": ""}), D93_TEXT)   # END이고 known이 비면 재료가 없다

    def test_any_material_is_used_instead(self):
        self.assertNotEqual(mr.fallback_text(B_END | {"known": "최근 일주일 2일"}), D93_TEXT)


if __name__ == "__main__":
    unittest.main()
