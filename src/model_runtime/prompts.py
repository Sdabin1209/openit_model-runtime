"""프롬프트 조립과 render_prompt. 순수 함수(같은 입력이면 같은 문자열).

2.0.0(D74·D75): 시스템 프롬프트는 모델 패키지의 일부다. 이 모듈은 문장을 갖지 않고, 패키지의 시스템 프롬프트와
입력 JSON을 채팅 형식으로 조립하는 규칙(템플릿 버전)만 가진다. 모델은 해석과 문장 만들기만 하고 판단은 하지 않는다.

채팅 형식(두 템플릿 공통): Qwen3 ChatML에서 enable_thinking=False, add_generation_prompt=True를 한 것과 같다.
    <|im_start|>system\\n{시스템 프롬프트}<|im_end|>\\n<|im_start|>user\\n{입력}<|im_end|>\\n<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n

템플릿 `prompt-4`(기본, D77): 입력 직렬화가 학습 데이터(dataset_10_14)의 user 메시지와 글자 단위로 같다.
    json.dumps(객체, ensure_ascii=False)  (기본 구분자 ", " ": ", 비ASCII 그대로)
  - LORA_A: 최상위 키만 사전순으로 놓는다(current_question, question_id, state, user_utterance). 그 아래(`state` 안 포함)는
    호출자가 준 키 순서를 그대로 보존한다. `state`의 키 삽입 순서는 호출자(survey-service)가 학습 규칙대로 만들어야 한다.
  - LORA_B: 모든 수준의 키를 사전순으로 정렬한다(학습 데이터가 그렇다). 배열 원소 순서는 그대로다.
  최상위를 서버가 강제하는 근거: 최상위 키 순서는 계약상 의미가 없고, 호출자 구현마다 다른 순서가 글자 어긋남(품질 저하)이 되기 쉽다.
  A의 하위 순서를 강제하지 않는 근거: `state`의 순서는 학습 때의 삽입 순서이며 스키마로 복원할 수 없다.
  B를 전부 정렬하는 근거: B 입력의 객체는 키 순서에 의미가 없고 학습 데이터가 전 수준 정렬이다.
템플릿 `prompt-3`(폐기 예정, 호환용): 입력 JSON이 boundary_contracts.canonical의 JCS(공백 없음, 전 수준 정렬)다. 학습 데이터와 글자가
  달라(D77) 서버에서 학습한 어댑터에는 쓰지 않는다. 이전 릴리스를 읽을 수 있게만 남긴다.

확인한 것: prompt-4의 user 글자는 학습 서버 val(A 400행, B 300행 중 읽은 행)과 일치한다(scripts/check_prompt_matches_dataset.py).
채팅 형식은 Qwen3-4B tokenizer_config.json의 chat_template을 jinja2로 렌더한 결과와 같다(scripts/check_chat_template.py;
transformers의 apply_chat_template 자체는 실행하지 않았다). 학습 때 쓴 채팅 템플릿 설정은 서버 코드를 읽지 않아 미확인이다."""
from __future__ import annotations

import json
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


def _user_prompt3(role: str, input: Mapping) -> str:
    return canonicalize(dict(input)).decode("utf-8")


def _user_prompt4(role: str, input: Mapping) -> str:
    """학습 데이터와 같은 글자: json.dumps(ensure_ascii=False). A는 최상위만 정렬, B는 전 수준 정렬."""
    if role == "LORA_B":
        return json.dumps(dict(input), ensure_ascii=False, sort_keys=True)
    return json.dumps({k: input[k] for k in sorted(input)}, ensure_ascii=False)


# 버전 -> 사용자 메시지 직렬화. 채팅 형식은 모두 같다.
_USER = {"prompt-3": _user_prompt3, "prompt-4": _user_prompt4}
TEMPLATES = {v: _qwen3_nothink for v in _USER}
SUPPORTED_TEMPLATE_VERSIONS = frozenset(TEMPLATES)
DEFAULT_TEMPLATE_VERSION = "prompt-4"
DEPRECATED_TEMPLATE_VERSIONS = frozenset({"prompt-3"})


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
    user = _USER[version](role, input)
    return TEMPLATES[version](system_prompt, user)
