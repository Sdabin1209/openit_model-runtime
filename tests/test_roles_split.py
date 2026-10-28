"""MR-01/MR-03: LoRA-A와 LoRA-B가 컴포넌트로 나뉘어 있고, 네 파일에 role 종류 분기가 없다."""
import re
import unittest
from pathlib import Path

import model_runtime as mr
from model_runtime import roles
from helpers import A_INPUT, B_INPUT, SCHEMA_DIR, cfgs_and_hashes

SRC = Path(mr.__file__).parent
BRANCH = re.compile(r"\brole\s*(==|!=|in\s+JSON_OUTPUT_ROLES|not in JSON_OUTPUT_ROLES)|JSON_OUTPUT_ROLES|\.role\s*(==|!=)")


class Split(unittest.TestCase):
    def test_no_role_branches_in_the_four_files(self):
        for f in ("session.py", "backends.py", "prompts.py", "schema_guard.py"):
            for n, line in enumerate((SRC / f).read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                self.assertIsNone(BRANCH.search(code), f"{f}:{n}: {line.strip()}")

    def test_two_role_objects_know_only_their_own_schemas(self):
        self.assertEqual({n: c.__name__ for n, c in roles.ROLE_TYPES.items()}, {"LORA_A": "ExtractRole", "LORA_B": "RespondRole"})
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        a, r = s._roles["LORA_A"], s._roles["LORA_B"]
        self.assertIsNotNone(a.constraint[0])           # A만 제약 스키마를 가진다
        self.assertEqual(r.constraint, (None, ()))
        self.assertIsInstance(a.call(b, A_INPUT).result, dict)
        self.assertEqual(r.call(b, B_INPUT).result.is_fallback, False)

    def test_retry_function_is_shared(self):
        self.assertTrue(callable(roles.run_attempts))


if __name__ == "__main__":
    unittest.main()
