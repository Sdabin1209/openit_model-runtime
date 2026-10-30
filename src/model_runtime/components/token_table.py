"""token_table 컴포넌트: `역할=토큰,역할=토큰` 글자를 역할 -> 토큰 표로 바꾼다. TokenTableInput -> TokenTable.

빈 역할·빈 토큰·형식 위반·역할/토큰 중복은 시작 거부(ValueError, 메시지에 값이 없다). 비밀 값(토큰)은 파일에서 읽어 이 함수에 넘기며 로그·오류에 싣지 않는다(D84).
스키마 밖 조건(schemas/README.md의 표): 역할 또는 토큰의 중복(JSON Schema로 객체 값의 유일성을 쓸 수 없다)."""
from __future__ import annotations

from typing import Mapping

INPUT, OUTPUT = "TokenTableInput", "TokenTable"


def run(req: Mapping) -> dict:
    out: dict = {}
    for kv in [x for x in (req["raw"] or "").split(",") if x.strip()]:
        role, sep, tok = kv.partition("=")
        role, tok = role.strip(), tok.strip()
        if not sep or not role or not tok:
            raise ValueError("MODEL_RUNTIME_TOKENS 항목은 `역할=토큰` 형식이고 역할과 토큰이 비어 있으면 안 된다")
        if role in out or tok in out.values():
            raise ValueError("MODEL_RUNTIME_TOKENS에 같은 역할 또는 같은 토큰이 두 번 있다")
        out[role] = tok
    if not out and not req["allow_empty"]:
        raise ValueError("MODEL_RUNTIME_TOKENS(또는 _FILE)가 필요하다. 인증 없이 열려면 개발용으로만 MODEL_RUNTIME_ALLOW_NO_AUTH=1")
    return out
