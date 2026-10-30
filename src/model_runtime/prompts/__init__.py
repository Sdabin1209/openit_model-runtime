"""옛 이름 model_runtime.prompts의 공개 이름을 새 위치(components/prompt_render, wiring/prompt_api)에서 다시 내보낸다(공개 API 유지, U400).
다른 영역이 `from model_runtime.prompts import DEFAULT_TEMPLATE_VERSION ...`로 읽는다."""
from ..components.prompt_render import (DEFAULT_TEMPLATE_VERSION, DEPRECATED_TEMPLATE_VERSIONS, SUPPORTED_TEMPLATE_VERSIONS, TEMPLATES,
                                        neutralize_special_tokens, serialize_extract_input_4, serialize_respond_input_4, _USER, _qwen3_nothink)
from ..wiring.prompt_api import render_prompt

__all__ = ["DEFAULT_TEMPLATE_VERSION", "DEPRECATED_TEMPLATE_VERSIONS", "SUPPORTED_TEMPLATE_VERSIONS", "TEMPLATES", "neutralize_special_tokens",
           "serialize_extract_input_4", "serialize_respond_input_4", "render_prompt"]
