"""구조 검사 (head/docs/DESIGN.md 6절, D117). 헤드의 head/scripts/structure-check.py와 같은 기준을 이 영역 시험으로도 강제한다."""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "model_runtime"
SCHEMAS = ROOT / "schemas"
HTTP = re.compile(r"^\s*(from|import)\s+(boundary_contracts\.clients|openit_link|urllib\.request|http\.client|http\.server|requests|waitress|wsgiref)\b", re.M)
ENTRY = {"__init__.py", "server.py"}   # 진입점·공개 이름 내보내기. 업무 규칙을 두지 않는다
EXPORT_PACKAGES = {"prompts", "schema_guard"}   # 옛 공개 이름을 새 위치에서 다시 내보내는 __init__.py 하나뿐인 패키지(공개 API 유지)


def code_files():
    return [f for f in PKG.rglob("*.py") if "__pycache__" not in f.parts]


def components():
    return sorted(f for f in (PKG / "components").glob("*.py") if f.name != "__init__.py")


class Layout(unittest.TestCase):
    def test_four_directories(self):
        for d in (SCHEMAS, SCHEMAS / "examples", PKG / "types", PKG / "components", PKG / "wiring"):
            self.assertTrue(d.is_dir(), d)

    def test_every_code_file_belongs_somewhere(self):   # 6절 6항
        for f in code_files():
            rel = f.relative_to(PKG)
            ok = rel.parts[0] in ("components", "wiring", "types") or rel.name in ENTRY or (rel.parts[0] in EXPORT_PACKAGES and rel.name == "__init__.py")
            self.assertTrue(ok, rel)

    def test_components_do_not_import_each_other(self):   # 6절 2항
        names = {f.stem for f in components()}
        for f in components():
            t = f.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s*(?:from|import)\s+(?:\.(?!\.)|model_runtime\.components\.?|\.\.components\.?)(\w+)?", t, re.M):
                other = m.group(1)
                self.assertFalse(other in names and other != f.stem, f"{f.name}이 {other}를 import")
            self.assertNotIn("from . import", t, f.name)

    def test_http_only_in_link_components(self):   # 6절 3항
        for f in code_files():
            if HTTP.search(f.read_text(encoding="utf-8")):
                self.assertTrue(f.name.startswith("link_") and f.parent.name == "components", f"{f.relative_to(PKG)}에 HTTP")

    def test_components_are_functions_with_schema_names(self):   # 규칙 4: 입력·출력 스키마를 이름으로 선언한다
        for f in components():
            t = f.read_text(encoding="utf-8")
            m = re.search(r'^INPUT, OUTPUT = "(\w+)", "(\w+)"', t, re.M)
            self.assertIsNotNone(m, f"{f.name}에 INPUT, OUTPUT 선언이 없다")
            for n in m.groups():
                self.assertTrue((SCHEMAS / f"{n}.json").is_file(), f"{f.name}: 스키마 {n} 없음")


def _validator(name, registry):
    return jsonschema.Draft202012Validator(json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8")), registry=registry)


class Examples(unittest.TestCase):
    def test_every_schema_has_valid_and_invalid_examples_with_right_results(self):   # 6절 4항
        res = [(f.name, Resource.from_contents(json.loads(f.read_text(encoding="utf-8")))) for f in SCHEMAS.glob("*.json")]
        reg = Registry().with_resources(res)
        for f in sorted(SCHEMAS.glob("*.json")):
            name = f.stem
            jsonschema.Draft202012Validator.check_schema(json.loads(f.read_text(encoding="utf-8")))
            valid = sorted((SCHEMAS / "examples").glob(f"{name}.valid*.json"))
            invalid = sorted((SCHEMAS / "examples").glob(f"{name}.invalid*.json"))
            self.assertTrue(valid and invalid, name)
            v = _validator(name, reg)
            for e in valid:
                self.assertTrue(v.is_valid(json.loads(e.read_text(encoding="utf-8"))), e.name)
            for e in invalid:
                self.assertFalse(v.is_valid(json.loads(e.read_text(encoding="utf-8"))), e.name)


class Generated(unittest.TestCase):
    def test_types_match_regeneration(self):   # 6절 5항
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "types"
            subprocess.run(["bash", str(ROOT / "scripts" / "gen-types.sh"), str(out)], check=True, capture_output=True)
            for f in sorted(out.glob("*.py")):
                self.assertEqual((PKG / "types" / f.name).read_text(encoding="utf-8"), f.read_text(encoding="utf-8"), f.name)
            self.assertEqual(sorted(p.name for p in out.glob("*.py")), sorted(p.name for p in (PKG / "types").glob("*.py")))


class OneSourcePerFact(unittest.TestCase):
    """같은 값이 스키마와 컴포넌트 두 곳에 있으면 어긋나지 않게 한다(스키마가 지원 값을 정하고 컴포넌트의 표가 같은 값을 갖는다)."""

    def test_supported_values_match_schemas(self):
        from model_runtime.components import fallback_text, prompt_render, survey_version_check, constraint_relax, release_registry
        s = lambda n: json.loads((SCHEMAS / f"{n}.json").read_text(encoding="utf-8"))
        self.assertEqual(set(s("PromptRenderInput")["properties"]["template_version"]["enum"]), set(prompt_render.SUPPORTED_TEMPLATE_VERSIONS))
        self.assertEqual(set(s("FallbackTextInput")["properties"]["policy_version"]["enum"]), set(fallback_text.PROTECTION_POLICIES))
        self.assertEqual(set(s("RespondCall")["properties"]["protection_policy_version"]["enum"]), set(fallback_text.PROTECTION_POLICIES))
        cfg = s("ServingConfigCheckInput")["properties"]["serving_config"]["properties"]
        self.assertEqual(set(cfg["prompt_template_version"]["enum"]), set(prompt_render.SUPPORTED_TEMPLATE_VERSIONS))
        self.assertEqual(set(cfg["protection_policy_version"]["enum"]), set(fallback_text.PROTECTION_POLICIES))
        self.assertEqual(cfg["constraint"]["properties"]["relax_policy_version"]["const"], constraint_relax.RELAX_POLICY_VERSION)
        self.assertEqual(cfg["output_contract"]["const"], survey_version_check.SUPPORTED_RUNTIME_CONTRACT_VERSION)
        self.assertEqual(release_registry.SUPPORTED_RUNTIME_CONTRACT_VERSION, survey_version_check.SUPPORTED_RUNTIME_CONTRACT_VERSION)
        self.assertEqual(int(re.search(r"\d+", s("SurveyVersionCheckInput")["properties"]["contract_version_text"]["pattern"]).group()), survey_version_check.SUPPORTED_RUNTIME_CONTRACT_VERSION)


if __name__ == "__main__":
    unittest.main()
