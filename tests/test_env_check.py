"""기동 때 환경 변수 선언 검증 (계약 ModelServingEnv, U73; ADR 0032). 우리 코드의 시험: 선언을 어떻게 불러 쓰는지(이름만 적기, 폐지 변수, 접두사 밖 변수 무시)를 본다.
선언 자체의 내용(어떤 이름이 허용되나)은 계약의 몫이라 여기서 다시 시험하지 않는다."""
import unittest
from pathlib import Path

from model_runtime import server
from model_runtime.components import env_check

from helpers import SCHEMA_DIR
GOOD = {"MODEL_RUNTIME_BACKEND": "fake", "MODEL_RUNTIME_ALLOW_FAKE": "1", "MODEL_RUNTIME_ALLOW_NO_AUTH": "1",
        "MODEL_RUNTIME_RELEASE_URL": "http://127.0.0.1:9", "MODEL_RUNTIME_SCHEMA_DIR": str(SCHEMA_DIR)}


def names(env):
    return {(v["name"], v["keyword"]) for v in env_check.run(env)["violations"]}


class EnvCheck(unittest.TestCase):
    def test_correct_environment_passes_and_unprefixed_variables_are_ignored(self):
        self.assertEqual(names({**GOOD, "PATH": "/usr/bin", "LANG": "C", "HOME": "/var/lib/model-runtime/home", "HF_HOME": "/hf"}), set())

    def test_unknown_prefixed_name_is_rejected_by_name(self):
        self.assertEqual(names({**GOOD, "MODEL_RUNTIME_X": "1"}), {("MODEL_RUNTIME_X", "undeclared")})
        self.assertEqual(names({**GOOD, "HF_NOT_DECLARED_X": "1"}), {("HF_NOT_DECLARED_X", "undeclared")})

    def test_retired_variables_are_rejected_because_they_are_not_declared(self):
        for old in ("MODEL_RUNTIME_REQUEST_DEADLINE_S", "MODEL_RUNTIME_FAKE_RISK_PHRASE"):
            self.assertEqual(names({**GOOD, old: "3"}), {(old, "undeclared")})

    def test_vllm_without_profile_is_rejected(self):
        env = {k: v for k, v in GOOD.items() if k not in ("MODEL_RUNTIME_BACKEND", "MODEL_RUNTIME_ALLOW_FAKE")}
        self.assertIn(("MODEL_RUNTIME_PROFILE", "required"), names({**env, "MODEL_RUNTIME_BACKEND": "vllm"}))
        self.assertIn(("MODEL_RUNTIME_PROFILE", "required"), names(env))   # 기본 백엔드가 vllm이다

    def test_format_violation_names_the_variable_not_the_value(self):
        self.assertEqual(names({**GOOD, "MODEL_RUNTIME_PORT": "secret-ish"}), {("MODEL_RUNTIME_PORT", "pattern")})

    def test_build_service_refuses_with_names_only(self):
        with self.assertRaises(SystemExit) as cm:
            server.build_service({**GOOD, "MODEL_RUNTIME_X": "token-value-123", "MODEL_RUNTIME_PORT": "token-value-456"})
        msg = str(cm.exception)
        self.assertIn("MODEL_RUNTIME_X(undeclared)", msg)
        self.assertIn("MODEL_RUNTIME_PORT(pattern)", msg)
        self.assertNotIn("token-value", msg)

    def test_missing_release_url_refuses_to_start(self):
        env = {k: v for k, v in GOOD.items() if k != "MODEL_RUNTIME_RELEASE_URL"}
        with self.assertRaises(SystemExit) as cm:
            server.build_service(env)
        self.assertIn("MODEL_RUNTIME_RELEASE_URL(required)", str(cm.exception))


class ImageEnv(unittest.TestCase):
    def test_every_prefixed_env_the_image_sets_is_declared(self):
        """Dockerfile의 ENV가 접두사 변수를 선언 밖으로 두면 이미지가 뜨자마자 시작을 거부한다(스모크에서야 드러난 사고를 정적으로 막는다)."""
        import re
        df = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
        env = dict(re.findall(r"\b((?:MODEL_RUNTIME_|HF_)[A-Z0-9_]+)=(\S+)", "\n".join(l for l in df.splitlines() if not l.lstrip().startswith("#"))))
        self.assertTrue(env)
        bad = {n for n, k in names({**GOOD, **env})}
        self.assertEqual(bad, set())


if __name__ == "__main__":
    unittest.main()
