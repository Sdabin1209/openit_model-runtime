"""라이브러리 예외. BoundaryError가 아니다(INTERFACES I9)."""
from __future__ import annotations


class ModelRuntimeError(Exception):
    pass


class ContractViolation(ModelRuntimeError):
    """입력 또는 serving_config가 형식을 위반했다. violations는 (JSON 포인터 경로, 키워드) 목록이며 값은 담지 않는다."""

    def __init__(self, what: str, violations: list[tuple[str, str]] | None = None):
        self.violations = list(violations or [])
        detail = "; ".join(f"{p or '/'}:{k}" for p, k in self.violations[:5])
        super().__init__(f"{what}" + (f" ({detail})" if detail else ""))


class ContractVersionMismatch(ModelRuntimeError):
    """schema_dir의 문진 스키마 정수 버전(CONTRACT_VERSION)이 SUPPORTED_RUNTIME_CONTRACT_VERSION과 다르거나 읽을 수 없다."""


class UnsupportedServingConfig(ModelRuntimeError):
    """지원하지 않는 프롬프트 템플릿·디코딩·계약·백엔드 설정."""


class BackendNotAvailable(UnsupportedServingConfig):
    """설정이 가리키는 백엔드를 이 환경에서 쓸 수 없다."""


class ServingConfigMismatch(ModelRuntimeError):
    """serving_config_hash가 expected_serving_config_hash와 다르다."""
