"""입출력 검증, 문진 스키마 정수 버전 확인, 제약용 스키마 완화.

2.0.0(D74): 모델 입출력 스키마(`ActionExtractionInput`, `ActionExtraction`, `ResponseRequest`, `ResponseText`)는
boundary-contracts 패키지에서 가져온다(`load_schema`, `validator`, 복사본 없음). 문진 스키마 배포 디렉터리(`schema_dir`)는
`runtime_contract_version`(문진 스키마 정수 버전, D75)을 확인하는 데만 쓴다."""
from __future__ import annotations

import copy
import json
import re
import warnings
from pathlib import Path
from typing import Any, Iterable

import jsonschema
from boundary_contracts import load_schema, validator

from .errors import ContractVersionMismatch, ContractViolation

SUPPORTED_RUNTIME_CONTRACT_VERSION = 1
RELAX_POLICY_VERSION = "relax-3"

INPUT_CONTRACT = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
OUTPUT_CONTRACT = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}


def violations_of(errors: Iterable[jsonschema.ValidationError]) -> list[tuple[str, str]]:
    out = []
    for e in errors:
        path = "/" + "/".join(map(str, e.absolute_path)) if e.absolute_path else ""
        out.append((path, str(e.validator)))
    return sorted(set(out))


def _strip_comments(node: Any) -> Any:
    """제약 디코딩 엔진에 넘기는 스키마에서 설명용 `$comment`를 뺀다(검증에는 영향이 없다)."""
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if k != "$comment"}
    if isinstance(node, list):
        return [_strip_comments(v) for v in node]
    return node


_END_ANCHOR = re.compile(r"(?<!\\)\$")


def _strict_pattern_validator(v):
    """`pattern`의 `$`는 파이썬 `re`에서 끝 줄바꿈 앞에도 맞는다(`Q4.answer\\n`이 통과, MR-14). 검증기에서만 `$`를 `\\Z`로 읽는다.
    계약 스키마 원본은 바꾸지 않는다(제약 디코딩에 주는 스키마도 그대로)."""
    def pattern(validator_, patrn, instance, schema):
        if validator_.is_type(instance, "string") and not re.search(_END_ANCHOR.sub(r"\\Z", patrn), instance):
            yield jsonschema.ValidationError(f"does not match {patrn!r}")
    cls = jsonschema.validators.extend(type(v), {"pattern": pattern})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return cls(v.schema, resolver=v.resolver)


class SchemaSet:
    def __init__(self, schema_dir: Path | str):
        """schema_dir: 문진 스키마의 배포 스키마 디렉터리(`<...>/schema/schemas`). CONTRACT_VERSION은 그 상위에서 읽는다.
        환경 변수와 전역 설정은 읽지 않는다. 모델 입출력 스키마는 이 디렉터리가 아니라 boundary_contracts에서 읽는다."""
        d = Path(schema_dir)
        try:
            self.contract_version = int((d.parent / "CONTRACT_VERSION").read_text().strip())
        except (OSError, ValueError):
            raise ContractVersionMismatch("문진 스키마 CONTRACT_VERSION을 읽을 수 없다") from None
        if self.contract_version != SUPPORTED_RUNTIME_CONTRACT_VERSION:
            raise ContractVersionMismatch(
                f"문진 스키마 버전 {self.contract_version}은 지원하지 않는다 (지원: {SUPPORTED_RUNTIME_CONTRACT_VERSION})"
            )
        self._validators = {name: _strict_pattern_validator(validator(name)) for name in [*INPUT_CONTRACT.values(), *OUTPUT_CONTRACT.values()]}

    def model_output_schema(self, role: str) -> dict:
        """제약 디코딩에 쓸 출력 스키마(완화 전). 제약을 쓸지는 역할이 정한다(이 클래스는 역할 종류를 가리지 않는다, MR-01)."""
        return _strip_comments(load_schema(OUTPUT_CONTRACT[role]))

    def validate_input(self, role: str, value: Any) -> None:
        name = INPUT_CONTRACT[role]
        errs = list(self._validators[name].iter_errors(value))
        if errs:
            raise ContractViolation(f"{name} 입력 계약 위반", violations_of(errs))

    def output_violations(self, role: str, value: Any) -> list[tuple[str, str]]:
        """출력 계약(ActionExtraction 또는 ResponseText)으로 엄격하게 검증한다. 위반이 없으면 빈 목록."""
        return violations_of(self._validators[OUTPUT_CONTRACT[role]].iter_errors(value))


# --- 완화 -----------------------------------------------------------------

_SCHEMA_MAPS = ("properties", "$defs", "patternProperties", "dependentSchemas")
_SCHEMA_SINGLE = ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames")
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NON_SCHEMA = ("enum", "const", "required", "type", "default", "examples")


MAX_VARIANTS = 64          # allOf if/then을 anyOf로 펼칠 때 한 객체의 변형 수 상한(초과하면 규칙을 모두 빼고 기록한다)
_FORBID = object()         # 변형 안에서 "이 속성은 없어야 한다"
_EXPAND_NEEDS = ("anyOf", "enum", "required", "properties")
_OUTER_KEYS = ("$schema", "$id", "title", "description", "$defs")


class _Unsupported(Exception):
    pass


def _jk(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _norm_domain(s: Any) -> list | None:
    """속성 스키마가 허용하는 값의 유한 집합(enum/const)이면 목록, 아니면 None."""
    if isinstance(s, dict):
        if isinstance(s.get("enum"), list):
            return list(s["enum"])
        if "const" in s:
            return [s["const"]]
    return None


def _intersect(a: dict, b: dict) -> dict | None:
    """두 속성 스키마의 교집합(둘 다 만족). 비면 None. 같은 키워드가 서로 다른 모양이면 _Unsupported."""
    out = dict(a)
    for k, v in b.items():
        if k not in out:
            out[k] = v
        elif k == "enum":
            keep = {_jk(x) for x in v}
            out[k] = [x for x in out[k] if _jk(x) in keep]
            if not out[k]:
                return None
        elif k == "type":
            ta = [out[k]] if isinstance(out[k], str) else list(out[k])
            tb = [v] if isinstance(v, str) else list(v)
            t = [x for x in ta if x in tb or (x == "integer" and "number" in tb)]
            if not t:
                return None
            out[k] = t[0] if len(t) == 1 else t
        elif k in ("minItems", "minLength", "minimum"):
            out[k] = max(out[k], v)
        elif k in ("maxItems", "maxLength", "maximum"):
            out[k] = min(out[k], v)
        elif _jk(out[k]) != _jk(v):
            raise _Unsupported(f"keyword {k}")
    return out


def _clean(s: Any, feats: frozenset, changed: set) -> Any:
    """then/else 안 속성 스키마를 변형에 쓸 수 있게 한다: const는 enum으로, 엔진이 모르는 키워드는 뺀다(기록)."""
    if s is True or s is False:
        return s
    if not isinstance(s, dict):
        raise _Unsupported("property schema shape")
    out = {}
    for k, v in s.items():
        if k == "const":
            out["enum"] = [v]
        elif k in ("enum", "type", "minItems", "maxItems", "minLength", "minimum", "pattern") :
            if k in feats:
                out[k] = v
            else:
                changed.add(k)
        elif k in ("title", "description", "$comment"):
            continue
        else:
            raise _Unsupported(f"property keyword {k}")
    return out


def _rule_branches(rule: Any, base_props: dict, feats: frozenset, changed: set) -> list[list[tuple]]:
    """if/then/else 한 규칙을 '가지' 목록으로 바꾼다. 가지 하나는 단언 목록이고, 가지들은 서로 겹치지 않으며 합치면 규칙과 같다."""
    if not isinstance(rule, dict) or "if" not in rule or set(rule) - {"if", "then", "else"}:
        raise _Unsupported("not an if/then/else rule")
    cond = rule["if"]
    if not isinstance(cond, dict) or set(cond) - {"properties", "required"} or not isinstance(cond.get("properties"), dict) \
            or len(cond["properties"]) != 1:
        raise _Unsupported("if is not a single-property check")
    (p, cs), = cond["properties"].items()
    req = cond.get("required", [])
    if req not in ([], [p]):
        raise _Unsupported("if required shape")
    if not isinstance(cs, dict) or set(cs) - {"enum", "const"} or _norm_domain(cs) is None:
        raise _Unsupported("if is not const/enum")
    in_set = _norm_domain(cs)
    dom = _norm_domain(base_props.get(p))
    if dom is None:
        raise _Unsupported(f"no finite domain for {p}")
    keep = {_jk(x) for x in in_set}
    comp = [x for x in dom if _jk(x) not in keep]

    def side(branch: Any) -> list[tuple]:
        if branch is None or branch is True:
            return []
        if branch is False:
            return [("never",)]
        if not isinstance(branch, dict) or set(branch) - {"properties", "required"}:
            raise _Unsupported("then/else shape")
        out: list[tuple] = []
        for r in branch.get("required", []):
            out.append(("require", r))
        for name, ps in (branch.get("properties") or {}).items():
            out.append(("schema", name, _clean(ps, feats, changed)))
        return out

    c_branch = ([("require", p)] if req else []) + [("schema", p, {"enum": in_set})] + side(rule.get("then"))
    n_branch = [("schema", p, {"enum": comp})] + side(rule.get("else"))
    return [c_branch, n_branch]


def _apply(state: tuple, assertion: tuple, closed: bool, declared: set) -> tuple | None:
    props, req = state
    kind = assertion[0]
    if kind == "never":
        return None
    name = assertion[1]
    props = dict(props)
    undeclared_closed = closed and name not in declared
    if kind == "require":
        if props.get(name) is _FORBID or undeclared_closed:
            return None
        return props, req | {name}
    schema = assertion[2]
    if schema is True:
        return props, req
    if schema is False or undeclared_closed or props.get(name) is _FORBID:
        if name in req:
            return None
        props[name] = _FORBID
        return props, req
    merged = _intersect(props.get(name, {}), schema)
    if merged is None:
        if name in req:
            return None
        props[name] = _FORBID
    else:
        props[name] = merged
    return props, req


def _expand_allof(obj: dict, rules: list, path: str, feats: frozenset, changed: set) -> dict | None:
    """객체 스키마 obj(allOf는 이미 뺐다)와 그 allOf 규칙들을 같은 뜻의 anyOf 변형들로 펼친다. 규칙별로 못 펼치는 모양은 그 규칙만
    빼고 기록한다(넓힘). 변형 수가 MAX_VARIANTS를 넘으면 _Unsupported(호출자가 규칙을 모두 빼고 기록)."""
    if "anyOf" in obj:
        raise _Unsupported("object already has anyOf")
    base_props = obj.get("properties", {})
    closed = obj.get("additionalProperties") is False
    all_rules = []
    for i, rule in enumerate(rules):
        try:
            all_rules.append(_rule_branches(rule, base_props, feats, changed))
        except _Unsupported as e:
            changed.add(f"allOf@{path or '/'}[{i}]:{e}")
    states = [(copy.deepcopy(base_props), frozenset(obj.get("required", [])))]
    declared = set(base_props)
    for branches in all_rules:
        nxt, seen = [], set()
        for st in states:
            for br in branches:
                cur: tuple | None = st
                for a in br:
                    cur = _apply(cur, a, closed, declared)
                    if cur is None:
                        break
                if cur is None:
                    continue
                key = (_jk({k: ("#F" if v is _FORBID else v) for k, v in cur[0].items()}), tuple(sorted(cur[1])))
                if key not in seen:
                    seen.add(key)
                    nxt.append(cur)
        states = nxt
        if len(states) > MAX_VARIANTS:
            raise _Unsupported(f"variants>{MAX_VARIANTS}")
    if not states:
        raise _Unsupported("no satisfiable variant")
    outer = {k: v for k, v in obj.items() if k in _OUTER_KEYS}
    inner = {k: v for k, v in obj.items() if k not in _OUTER_KEYS and k not in ("properties", "required")}
    variants = []
    for props, req in states:
        v = dict(inner)
        pr = {}
        for n, o in props.items():
            if o is _FORBID:
                if not closed:
                    pr[n] = False
            else:
                pr[n] = o
        if pr:
            v["properties"] = pr
        order = list(obj.get("required", [])) + sorted(req - set(obj.get("required", [])))
        if order:
            v["required"] = order
        variants.append(v)
    outer["anyOf"] = variants
    return outer


def relax(schema: dict, supported_features: Iterable[str]) -> tuple[dict, list[str]]:
    """엔진이 지원하지 않는 키워드를 허용 범위를 넓히는 방향으로만 바꾼다.

    - 지원하지 않는 oneOf: 가지가 모두 const면 enum으로(같은 뜻), 아니면 anyOf로(넓힘), anyOf도 없으면 뺀다.
    - 지원하지 않는 allOf(relax-3, D83): 규칙이 `if`가 한 속성의 const/enum 검사인 if/then(/else)이고 anyOf·enum이 지원되면
      같은 뜻의 anyOf 변형들로 펼친다(`allOf->anyOf`). 못 펼치는 규칙은 그 규칙만 빼고 `allOf@<경로>[<번호>]:<이유>`로 기록하며,
      변형 수가 MAX_VARIANTS를 넘으면 그 객체의 규칙을 모두 뺀다(`allOf@<경로>:variants>N`). 펼치다 엔진이 모르는 키워드(예 maxItems)는
      빼고 그 이름을 기록한다.
    - 그 밖의 지원하지 않는 키워드는 뺀다. if/then/else는 함께 다룬다.
    순수 함수다. 돌려주는 목록은 바뀐 키워드(정렬, 중복 없음)다."""
    feats = frozenset(supported_features)
    changed: set[str] = set()
    can_expand = all(k in feats for k in _EXPAND_NEEDS)

    def node(s: Any, path: str = "") -> Any:
        if not isinstance(s, dict):
            return s
        out: dict[str, Any] = {}
        rules = None
        for k, v in s.items():
            if k in _NON_SCHEMA:
                if k in ("enum", "const", "required", "type") and k not in feats and k != "type":
                    changed.add(k)
                    continue
                out[k] = v
            elif k == "oneOf" and "oneOf" not in feats:
                if all(isinstance(b, dict) and set(b) == {"const"} for b in v) and "enum" in feats:
                    out["enum"] = [b["const"] for b in v]
                    changed.add("oneOf->enum")
                elif "anyOf" in feats:
                    out["anyOf"] = [node(b, f"{path}/oneOf/{i}") for i, b in enumerate(v)]
                    changed.add("oneOf->anyOf")
                else:
                    changed.add("oneOf")
            elif k in ("if", "then", "else") and "if" not in feats:
                changed.add(k)
            elif k == "allOf" and "allOf" not in feats:
                if can_expand and isinstance(v, list) and s.get("type") == "object":
                    rules = v
                else:
                    changed.add(k)
            elif k in _SCHEMA_LISTS:
                if k not in feats and k != "oneOf":
                    changed.add(k)
                else:
                    out[k] = [node(b, f"{path}/{k}/{i}") for i, b in enumerate(v)]
            elif k in _SCHEMA_MAPS:
                out[k] = {n: node(b, f"{path}/{n}") for n, b in v.items()}
            elif k in _SCHEMA_SINGLE:
                if k not in feats:
                    changed.add(k)
                else:
                    out[k] = node(v, f"{path}/{k}") if isinstance(v, dict) else v
            else:
                if k.startswith("$") or k in feats or k in ("title", "description"):
                    out[k] = v
                else:
                    changed.add(k)
        if rules is not None:
            local: set[str] = set()
            try:
                res = _expand_allof(out, rules, path, feats, local)
            except _Unsupported as e:
                changed.add(f"allOf@{path or '/'}:{e}")
            else:
                changed.add("allOf->anyOf")
                changed.update(local)
                return res
        return out

    return node(schema), sorted(changed)
