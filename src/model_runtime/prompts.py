"""프롬프트 조립과 render_prompt. 순수 함수(같은 입력이면 같은 문자열).

2.0.0(D74·D75): 시스템 프롬프트는 모델 패키지의 일부다. 이 모듈은 문장을 갖지 않고, 패키지의 시스템 프롬프트와
입력 JSON을 채팅 형식으로 조립하는 규칙(템플릿 버전)만 가진다. 모델은 해석과 문장 만들기만 하고 판단은 하지 않는다.

템플릿 `prompt-3`: Qwen3 채팅 템플릿(ChatML)에서 enable_thinking=False로 add_generation_prompt=True를 한 것과 같게 쓴 문자열이다.
    <|im_start|>system\\n{시스템 프롬프트}<|im_end|>\\n<|im_start|>user\\n{입력 JSON}<|im_end|>\\n<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n
입력 JSON은 boundary_contracts.canonical의 JCS 직렬화(키 사전순, 공백 없음, UTF-8)다.
확인하지 못한 것: (1) 이 문자열이 실제 Qwen3 토크나이저의 apply_chat_template 결과와 바이트까지 같은지(토크나이저·모델 파일 없이는 대조하지 못했다),
(2) 학습 데이터의 user 메시지가 입력 JSON을 같은 방식(JCS)으로 직렬화했는지(학습 서버 데이터는 이 영역이 읽을 수 없다). 둘 다 미확인이다."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Mapping

from boundary_contracts.canonical import canonicalize

from .errors import ContractViolation, UnsupportedServingConfig
from .schema_guard import INPUT_CONTRACT, SchemaSet

def _qwen3_nothink(system: str, user: str) -> str:
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n")


TEMPLATES = {"prompt-3": _qwen3_nothink}
SUPPORTED_TEMPLATE_VERSIONS = frozenset(TEMPLATES)


@lru_cache(maxsize=8)
def _schemas_for(schema_dir: str) -> SchemaSet:
    return SchemaSet(Path(schema_dir))


def render_prompt(role: str, input: Mapping, *, serving_config: Mapping, system_prompt: str, schema_dir: Path,
                  schemas: SchemaSet | None = None) -> str:
    """I9 render_prompt. system_prompt는 모델 패키지의 프롬프트 파일 내용이다. schemas는 이미 읽은 SchemaSet을 재사용하려는 내부용 선택 인자다."""
    if role not in INPUT_CONTRACT:
        raise ContractViolation("role은 LORA_A 또는 LORA_B여야 한다")
    version = serving_config.get("prompt_template_version") if isinstance(serving_config, Mapping) else None
    if version not in TEMPLATES:
        raise UnsupportedServingConfig("지원하지 않는 프롬프트 템플릿 버전")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ContractViolation("system_prompt는 비어 있지 않은 문자열이어야 한다")
    s = schemas or _schemas_for(str(schema_dir))
    s.validate_input(role, input)
    user = canonicalize(dict(input)).decode("utf-8")
    return TEMPLATES[version](system_prompt, user)
