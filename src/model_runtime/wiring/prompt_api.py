"""공개 render_prompt (INTERFACES I9). 입력 검증(io_validate)과 조립(prompt_render)을 잇는다. 학습 변환·평가·서비스가 같은 함수로 같은 프롬프트를 만든다."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Mapping

from ..components import prompt_render
from .errors import ContractViolation, UnsupportedServingConfig
from .schema_gate import gate, violations
from .schema_set import SchemaSet

_render = gate(prompt_render)


@lru_cache(maxsize=8)
def _schemas_for(schema_dir: str) -> SchemaSet:
    return SchemaSet(Path(schema_dir))


def render_prompt(role: str, input: Mapping, *, serving_config: Mapping, system_prompt: str, schema_dir: Path,
                  schemas: SchemaSet | None = None) -> str:
    """I9 render_prompt. system_prompt는 모델 패키지의 프롬프트 파일 내용이다. schemas는 이미 읽은 SchemaSet을 재사용하려는 내부용 선택 인자다."""
    version = serving_config.get("prompt_template_version") if isinstance(serving_config, Mapping) else None
    bad = {p for p, _ in violations("PromptRenderInput", {"role": role, "template_version": version, "system_prompt": system_prompt, "input": {}})}
    if "/role" in bad:
        raise ContractViolation("role은 LORA_A 또는 LORA_B여야 한다")
    if "/template_version" in bad:
        raise UnsupportedServingConfig("지원하지 않는 프롬프트 템플릿 버전")
    if bad:
        raise ContractViolation("system_prompt는 비어 있지 않은 문자열이어야 한다")
    (schemas or _schemas_for(str(schema_dir))).validate_input(role, input)   # 검증 뒤, 직렬화 전(서버와 직접 호출자가 같은 프롬프트를 만든다)
    return _render({"role": role, "template_version": version, "system_prompt": system_prompt, "input": dict(input)})
