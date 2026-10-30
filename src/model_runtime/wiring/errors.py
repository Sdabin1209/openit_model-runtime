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
    """역할의 serving_config_hash가 그 역할의 기대 해시와 다르다. role에 어느 역할인지 담는다."""

    def __init__(self, role: str, message: str | None = None):
        self.role = role
        super().__init__(message or f"{role}의 serving_config_hash가 expected_serving_config_hashes[{role!r}]와 다르다")


class SystemPromptMismatch(ModelRuntimeError):
    """어댑터 디렉터리의 시스템 프롬프트 파일이 없거나 system_prompt.sha256과 다르다(D75). role에 어느 역할인지 담는다. 내용은 담지 않는다."""

    def __init__(self, role: str, message: str | None = None):
        self.role = role
        super().__init__(message or f"{role}의 시스템 프롬프트 파일이 없거나 해시가 다르다")


class ModelOutputInvalid(ModelRuntimeError):
    """extract(LoRA-A)가 재시도를 다 써도 ActionExtraction을 지키지 못했다(D75). 대체 출력을 만들지 않는다(값을 지어내지 않는다).

    attempts: 시도 횟수. errors: 마지막 시도의 오류 이름(경로와 키워드만, 값·발화 원문 없음).
    HTTP 서버는 이것을 503 UNAVAILABLE(details.reason=OUTPUT_INVALID)로 낸다."""

    def __init__(self, attempts: int, errors: tuple = ()):
        self.attempts, self.errors = attempts, tuple(errors)
        super().__init__(f"모델 출력이 ActionExtraction 형식을 지키지 못했다 (시도 {attempts}회)")


MAX_VIOLATION_NAMES = 20


def violation_names(errors) -> list[str]:
    """ModelOutputInvalid.errors("schema:<경로>:<키워드>" 또는 "json")를 응답·로그용 "<경로>:<키워드>" 목록으로 바꾼다(D82).
    경로와 키워드 이름만 낸다. 값·발화·모델 원문은 들어 있지 않다. 최대 20개, 정렬, 중복 없음."""
    out = {e[len("schema:"):] if e.startswith("schema:") else e for e in errors}
    return sorted(out)[:MAX_VIOLATION_NAMES]
