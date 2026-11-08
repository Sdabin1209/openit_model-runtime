"""스키마 문지기: 컴포넌트의 입력·출력을 `schemas/`의 JSON Schema(draft 2020-12)로 검증한다(DESIGN.md 5절).

왜 wiring인가: 컴포넌트끼리는 서로의 구조를 모르므로(규칙 3) 공통 검증 코드를 컴포넌트에 두지 않고 영역이 연결 지점에서 건다(규칙 5).
컴포넌트는 `INPUT`, `OUTPUT`(스키마 이름)과 `run`만 두고, 거부 조건을 코드에 따로 두지 않는다."""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Mapping

from referencing import Resource

from .errors import ContractViolation
import jsonschema
from boundary_contracts import format_checker as _bc_format_checker
from boundary_contracts.schemas_api import ContractValidator as _BcValidator, schema_registry as _bc_schema_registry  # 영역 스키마도 계약 검증 규칙(형식 강제, NaN·무한대 거부, 계약 registry)을 받는다(계약 5.0.1)


def _dir():
    """리포 체크아웃(src 레이아웃, 편집 설치·시험)에서는 루트 schemas/를, 설치물에서는 패키지 데이터 model_runtime.schemas를 읽는다(같은 파일, pyproject)."""
    root = Path(__file__).resolve().parents[3] / "schemas"
    return root if (root / "Role.json").is_file() else files("model_runtime.schemas")


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    """영역 내부 스키마 한 개. 정본은 리포 루트 schemas/이고 설치물에는 model_runtime.schemas로 들어 있다(pyproject)."""
    return json.loads((_dir() / f"{name}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _registry() -> Registry:
    res = []
    for f in _dir().iterdir():
        if f.name.endswith(".json"):
            res.append((f.name, Resource.from_contents(json.loads(f.read_text(encoding="utf-8")))))
    # 왜: 계약 registry(안정 URI https://openit.example/contracts/v5/…)에 이 영역 스키마 파일을 더한다. 영역 스키마의 ID 칸은 계약 Id 를 안정 URI 로 가리키므로(사본 금지, D117) 계약 문서를 복사하지 않는다
    return _bc_schema_registry().with_resources(res)


@lru_cache(maxsize=None)
def _validator(name: str):
    # 왜: 계약 ContractValidator(NaN·무한대를 값 전체에서 먼저 거부, 계약 5.0.1)로 감싸고 계약 registry 에 이 영역 스키마를 더한 registry 를 준다.
    #     boundary_contracts.schema_validator(schema).evolve(registry=…) 는 쓰지 않는다: jsonschema 의 evolve 가 이미 만든 resolver(계약 registry 만 가짐)를 이어 받아
    #     영역 스키마 사이 $ref(CallInfo.json 등)가 풀리지 않았다(확인함, ADR 0038). schema_validator 와 같은 구성(Draft202012Validator + format_checker)을 registry 만 바꿔 만든다.
    return _BcValidator(jsonschema.Draft202012Validator(schema(name), registry=_registry(), format_checker=_bc_format_checker(schema(name))))


def violations(name: str, value: Any) -> list[tuple[str, str]]:
    """(JSON 포인터 경로, 키워드) 목록. 값은 싣지 않는다. 없으면 통과."""
    out = set()
    for e in _validator(name).iter_errors(value):
        out.add(("/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else "", str(e.validator)))
    return sorted(out)


def check(name: str, value: Any, exc: Callable[..., Exception] = ContractViolation, what: str | None = None) -> None:
    v = violations(name, value)
    if v:
        raise exc(what or f"{name} 위반") if exc is not ContractViolation else ContractViolation(what or f"{name} 위반", v)


def gate(component, exc: Callable[..., Exception] = ContractViolation, out_exc: Callable[..., Exception] = RuntimeError,
         output: str | None = None, run: str = "run") -> Callable[..., Any]:
    """컴포넌트 모듈의 run을 입력 검증 -> 실행 -> 출력 검증으로 감싼다. 내는 쪽이 내기 전에 검증하고 받는 쪽은 다시 검증하지 않는다(R7)."""
    fn = getattr(component, run)

    def call(x: Mapping | Any, **ports):   # ports: 바깥 자원(백엔드, 시계)과 다른 컴포넌트의 함수. wiring이 주입한다
        check(component.INPUT, x, exc)
        y = fn(x, **ports)
        check(output or component.OUTPUT, y, out_exc, f"{component.__name__} 출력이 {output or component.OUTPUT}를 지키지 않는다(구현 오류)")
        return y
    return call
