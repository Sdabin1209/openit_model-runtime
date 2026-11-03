"""env_check 컴포넌트: 프로세스 환경 변수가 계약 ModelServingEnv 선언을 지키는지 본다. EnvCheckInput -> EnvCheckResult.

왜 선언으로 검증하나: 영역 사이 설정 이름은 계약 선언에만 있다(U73). 선언에 없는 MODEL_RUNTIME_·HF_ 이름(오타, 폐지된 변수)을 조용히 무시하면
"설정했는데 안 먹는" 사고가 나므로 시작을 거부한다. 폐지 변수 목록을 이 영역이 따로 들지 않는다(선언에 없으므로 거부된다).
선언이 접두사 없이 선언한 캐시 변수(HOME, XDG_CACHE_HOME 등)는 선언의 properties에서 읽어 함께 본다. 그 밖의 변수(PATH 등)는 보지 않는다.
위반에는 변수 이름과 키워드만 싣는다. 값은 싣지 않는다(토큰이 섞일 수 있다)."""
from __future__ import annotations

from typing import Mapping

from boundary_contracts import load_schema, validator

INPUT, OUTPUT = "EnvCheckInput", "EnvCheckResult"
PREFIXES = ("MODEL_RUNTIME_", "HF_")


def _watched(environ: Mapping[str, str]) -> dict:
    declared = load_schema("ModelServingEnv")["properties"]   # 접두사 없는 선언(HOME 등)도 선언에서 읽는다
    return {k: v for k, v in environ.items() if k.startswith(PREFIXES) or k in declared}


def _name_keyword(e) -> tuple[str, str]:
    path = list(e.absolute_path)
    if path:   # 값 하나가 형식을 어김(패턴, enum 등)
        return str(path[0]), str(e.validator)
    if list(e.schema_path)[:1] == ["propertyNames"]:   # 선언에 없는 이름. e.instance는 값이 아니라 이름이다
        return str(e.instance), "undeclared"
    if e.validator == "required":
        return "|".join(e.validator_value), "required"
    if e.validator == "anyOf":   # 토큰 세 가지 중 하나는 있어야 한다
        return "MODEL_RUNTIME_TOKENS_FILE|MODEL_RUNTIME_TOKENS|MODEL_RUNTIME_ALLOW_NO_AUTH", "anyOf"
    return "(env)", str(e.validator)


def run(req: Mapping) -> dict:
    found = {_name_keyword(e) for e in validator("ModelServingEnv").iter_errors(_watched(req))}
    return {"violations": [{"name": n, "keyword": k} for n, k in sorted(found)]}
