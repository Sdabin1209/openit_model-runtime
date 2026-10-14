"""프롬프트 템플릿과 render_prompt. 순수 함수.

모델은 해석(파서)과 문장 만들기(생성기)만 한다. 저장·정정·충돌 판정, 다음 질문 결정은 코드(turn-engine)가 한다."""
from __future__ import annotations

from functools import lru_cache
from typing import Mapping

from boundary_contracts.canonical import canonicalize
from .errors import ContractViolation, UnsupportedServingConfig
from .schema_guard import INPUT_CONTRACT, SchemaSet

_V1 = {
    "LORA_A": (
        "너는 문진 발화 해석기다. 사용자 발화를 (대상, 값, 원문, 의도) 항목들로 바꾼다.\n"
        "발화에 있는 내용만 옮기고, 값이 맞는지·저장할지·다음에 무엇을 물을지는 판단하지 않는다.\n"
        "출력은 ParserOutput JSON 하나뿐이다.\n"
        "### 입력\n{input}\n### 출력\n"
    ),
    "LORA_B": (
        "너는 문진 응답 문장 작성기다. 응답 지시를 사용자 발화에 맞춰 자연스러운 한국어 문장으로 바꾼다.\n"
        "지시에 없는 내용을 추가하지 않고, 무엇을 말할지는 정하지 않는다.\n"
        "출력은 GeneratorOutput JSON 하나뿐이다.\n"
        "### 입력\n{input}\n### 출력\n"
    ),
}
TEMPLATES: dict[str, dict[str, str]] = {"prompt-1": _V1}
SUPPORTED_TEMPLATE_VERSIONS = frozenset(TEMPLATES)


@lru_cache(maxsize=8)
def _schemas_for(schema_dir: str | None) -> SchemaSet:
    return SchemaSet(schema_dir)


def render_prompt(role: str, input: Mapping, *, serving_config: Mapping, schema_dir=None, schemas: SchemaSet | None = None) -> str:
    if role not in INPUT_CONTRACT:
        raise ContractViolation("role은 LORA_A 또는 LORA_B여야 한다")
    version = serving_config.get("prompt_template_version") if isinstance(serving_config, Mapping) else None
    if version not in TEMPLATES:
        raise UnsupportedServingConfig("지원하지 않는 프롬프트 템플릿 버전")
    (schemas or _schemas_for(str(schema_dir) if schema_dir is not None else None)).validate_input(role, input)
    body = canonicalize(dict(input)).decode("utf-8")
    return TEMPLATES[version][role].replace("{input}", body)
