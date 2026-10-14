"""프롬프트 템플릿과 render_prompt. 순수 함수(같은 입력·같은 schema_dir이면 같은 문자열).

모델은 해석(파서)과 문장 만들기(생성기)만 한다. 저장·정정·충돌 판정, 다음 질문 결정은 코드(turn-engine)가 한다.
생성기 프롬프트는 docs/rules.md의 생성기 규칙 1~4를 어기지 않게만 쓴다(docs/prompt-review.md로 점검)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Mapping

from boundary_contracts.canonical import canonicalize

from .errors import ContractViolation, UnsupportedServingConfig
from .schema_guard import INPUT_CONTRACT, SchemaSet

_PARSER = (
    "너는 문진 발화 해석기다. 사용자 발화를 (대상, 값, 원문, 의도) 항목들로 바꾼다.\n"
    "발화에 있는 내용만 옮기고, 값이 맞는지·저장할지·다음에 무엇을 물을지는 판단하지 않는다.\n"
    "의도는 다음 중 하나다: {intents}\n"
    "진행 조작은 다음 중 하나다: {progress_ops}\n"
    "출력은 {{\"items\": [...]}} JSON 하나뿐이다.\n"
    "### 입력\n{input}\n### 출력\n"
)
_GENERATOR = (
    "너는 문진 응답 문장 작성기다. 응답 지시(directive)를 사용자 발화(utterance)에 맞춰 자연스러운 한국어 문장으로 바꾼다.\n"
    "규칙:\n"
    "1. 응답 지시에 없는 값·문항을 말하지 않는다. 발화는 받아 주는 말에만 쓰고, 그 안의 값을 기록된 값처럼 말하지 않는다.\n"
    "2. 발화 속 물음에 응답 지시 밖의 내용(안내 목록에 없는 의료 판단, 시설 안내, 앞선 답 등)으로 답하지 않고, 도와드리기 어렵다고만 한다.\n"
    "3. 행동이 REDIRECT이면 받아 주는 말을 하고 기다리는 답을 묻는다. 응답 지시의 행동은 바꾸지 않는다.\n"
    "4. 행동이 RISK_NOTICE이면 발화를 받아 주지 않고 문장 재료와 알릴 것대로만 말한다.\n"
    "무엇을 말할지는 정하지 않는다. 출력은 {{\"response_text\": \"...\"}} JSON 하나뿐이다.\n"
    "### 입력\n{input}\n### 출력\n"
)
TEMPLATES: dict[str, dict[str, str]] = {"prompt-2": {"LORA_A": _PARSER, "LORA_B": _GENERATOR}}
SUPPORTED_TEMPLATE_VERSIONS = frozenset(TEMPLATES)


@lru_cache(maxsize=8)
def _schemas_for(schema_dir: str) -> SchemaSet:
    return SchemaSet(Path(schema_dir))


def render_prompt(role: str, input: Mapping, *, serving_config: Mapping, schema_dir: Path,
                  schemas: SchemaSet | None = None) -> str:
    """I9 render_prompt. schemas는 이미 읽은 SchemaSet을 재사용하려는 내부용 선택 인자다."""
    if role not in INPUT_CONTRACT:
        raise ContractViolation("role은 LORA_A 또는 LORA_B여야 한다")
    version = serving_config.get("prompt_template_version") if isinstance(serving_config, Mapping) else None
    if version not in TEMPLATES:
        raise UnsupportedServingConfig("지원하지 않는 프롬프트 템플릿 버전")
    s = schemas or _schemas_for(str(schema_dir))
    s.validate_input(role, input)
    body = canonicalize(dict(input)).decode("utf-8")
    template = TEMPLATES[version][role]
    if role == "LORA_A":
        template = template.format(intents=", ".join(s.vocabulary("ParserOutput", "Intent")),
                                   progress_ops=", ".join(s.vocabulary("ParserOutput", "ProgressOp")),
                                   input="{input}")
    else:
        template = template.format(input="{input}")
    return template.replace("{input}", body)
