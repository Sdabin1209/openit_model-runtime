"""wiring이 컴포넌트에 주입하는 바깥 자원의 모양(포트). 구현은 백엔드 컴포넌트(fake_backend, mimic_backend, vllm_backend)다."""
from __future__ import annotations

from typing import Protocol

from ..types.GenerationRequest import GenerationRequest


class ConstraintBackend(Protocol):
    name: str
    version: str
    supported_features: frozenset

    def generate(self, request: GenerationRequest) -> str: ...
