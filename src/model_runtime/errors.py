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


class SchemaDirRequired(ModelRuntimeError):
    """문진 스키마 위치를 알 수 없다. 호출자가 schema_dir를 넘겨야 한다."""


class UnsupportedServingConfig(ModelRuntimeError):
    """지원하지 않는 프롬프트 템플릿·디코딩·계약·백엔드 설정."""


class BackendNotAvailable(UnsupportedServingConfig):
    """설정이 가리키는 백엔드를 이 환경에서 쓸 수 없다."""


class ServingConfigMismatch(ModelRuntimeError):
    """serving_config_hash가 expected_serving_config_hash와 다르다."""


class ModelOutputInvalid(ModelRuntimeError):
    """max_attempts까지 재시도했으나 출력이 스키마를 통과하지 못했다.

    발화와 모델 출력 원문은 담지 않는다. attempt_violations[k]는 k번째 시도의 (경로, 키워드) 목록이다.
    """

    def __init__(self, role: str, attempts: int, attempt_violations: list[list[tuple[str, str]]]):
        self.role = role
        self.attempts = attempts
        self.attempt_violations = attempt_violations
        last = attempt_violations[-1][:3] if attempt_violations else []
        detail = "; ".join(f"{p or '/'}:{k}" for p, k in last)
        super().__init__(f"{role} 출력이 {attempts}회 시도 후에도 스키마를 통과하지 못함 (마지막: {detail})")
