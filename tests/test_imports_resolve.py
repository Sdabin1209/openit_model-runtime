"""재발 방지(wo-401): 패키지의 모든 .py를 AST로 훑어, 함수 안의 늦은 import까지 포함해 model_runtime의 모든 상대·절대 import가 실제 모듈과 이름으로 풀리는지 정적으로 본다.
왜: 파일을 옮길 때 server.py의 늦은 import(`from .vllm_backend import ...`)가 이전 경로로 남았는데, GPU 경로라 시험이 실행으로는 못 잡았다."""
import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "model_runtime"


def module_file(dotted: str):
    """model_runtime.a.b -> 파일(a/b.py 또는 a/b/__init__.py). 없으면 None."""
    p = PKG.joinpath(*dotted.split(".")[1:])
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def defined_names(f: Path) -> set:
    names = set()
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return names


class ImportsResolve(unittest.TestCase):
    def test_every_model_runtime_import_resolves(self):
        checked = 0
        for f in sorted(PKG.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            pkg_parts = ["model_runtime", *f.relative_to(PKG).parent.parts]   # 이 파일이 속한 패키지
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):   # ast.walk는 함수 안의 import도 훑는다
                if isinstance(n, ast.ImportFrom):
                    if n.level:
                        base = pkg_parts[:len(pkg_parts) - (n.level - 1)]
                        mod = ".".join(base + ([n.module] if n.module else []))
                    elif n.module and n.module.split(".")[0] == "model_runtime":
                        mod = n.module
                    else:
                        continue
                    target = module_file(mod)
                    self.assertIsNotNone(target, f"{f.relative_to(PKG)}:{n.lineno}: 모듈 {mod}가 없다")
                    for a in n.names:
                        if a.name == "*":
                            continue
                        sub = module_file(f"{mod}.{a.name}")   # from 패키지 import 모듈
                        self.assertTrue(sub is not None or a.name in defined_names(target), f"{f.relative_to(PKG)}:{n.lineno}: {mod}에 {a.name}이 없다")
                        checked += 1
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name.split(".")[0] == "model_runtime":
                            self.assertIsNotNone(module_file(a.name), f"{f.relative_to(PKG)}:{n.lineno}: 모듈 {a.name}이 없다")
                            checked += 1
        self.assertGreater(checked, 50)


if __name__ == "__main__":
    unittest.main()
