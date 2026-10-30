"""MR-01/MR-03: LoRA-A와 LoRA-B가 컴포넌트(extract_role, respond_role)로 나뉘어 있고, 공용 파일에 role 종류 분기가 없다."""
import re
import unittest
from pathlib import Path

import model_runtime as mr
from model_runtime.components import extract_role, respond_role, retry_loop
from model_runtime.wiring.session import _Session
from helpers import A_FIELDS, A_FLOW, A_INPUT, B_INPUT, SCHEMA_DIR, cfgs_and_hashes

SRC = Path(mr.__file__).parent
BRANCH = re.compile(r"\brole\s*(==|!=|in\s+JSON_OUTPUT_ROLES|not in JSON_OUTPUT_ROLES)|JSON_OUTPUT_ROLES|\.role\s*(==|!=)")


class Split(unittest.TestCase):
    def test_no_role_branches_in_the_shared_files(self):
        for f in ("wiring/session.py", "components/fake_backend.py", "components/prompt_render.py", "components/constraint_relax.py",
                  "components/io_validate.py", "components/retry_loop.py", "components/mimic_backend.py"):
            for n, line in enumerate((SRC / f).read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                self.assertIsNone(BRANCH.search(code), f"{f}:{n}: {line.strip()}")

    def test_two_role_components_know_only_their_own_schemas(self):
        self.assertEqual((extract_role.INPUT, extract_role.OUTPUT), ("ExtractCall", "ExtractOutcome"))
        self.assertEqual((respond_role.INPUT, respond_role.OUTPUT), ("RespondCall", "RespondOutcome"))
        b = mr.FakeBackend(); cfgs, hs = cfgs_and_hashes(b)
        s = mr.FakeModelSession(cfgs, schema_dir=SCHEMA_DIR, expected_serving_config_hashes=hs, backend=b)
        self.assertIsNotNone(s._extract_call(A_INPUT, A_FLOW, A_FIELDS, None)["constraint_schema"])   # A만 제약 스키마를 가진다
        self.assertNotIn("constraint_schema", s._respond_call(B_INPUT, None))
        self.assertIsInstance(s.extract(A_INPUT, A_FLOW, A_FIELDS), dict)
        self.assertEqual(s.respond(B_INPUT).is_fallback, False)

    def test_retry_function_is_shared(self):
        self.assertTrue(callable(retry_loop.run))


if __name__ == "__main__":
    unittest.main()
