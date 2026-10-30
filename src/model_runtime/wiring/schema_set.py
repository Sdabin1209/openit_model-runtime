"""SchemaSet: 문진 스키마 정수 버전 확인과 역할별 입출력 검증의 공개 이름(옛 schema_guard.SchemaSet). 일은 컴포넌트가 하고 여기서는 연결만 한다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..components import io_validate, survey_version_check
from .errors import ContractVersionMismatch, ContractViolation
from .schema_gate import check, gate, violations

SUPPORTED_RUNTIME_CONTRACT_VERSION = survey_version_check.SUPPORTED_RUNTIME_CONTRACT_VERSION
_validate = gate(io_validate)
_version = gate(survey_version_check, ContractVersionMismatch)


class SchemaSet:
    def __init__(self, schema_dir: Path | str):
        """schema_dir: 문진 스키마의 배포 스키마 디렉터리(`<...>/schema/schemas`). CONTRACT_VERSION은 그 상위에서 읽는다.
        환경 변수와 전역 설정은 읽지 않는다. 모델 입출력 스키마는 이 디렉터리가 아니라 boundary_contracts에서 읽는다."""
        d = Path(schema_dir)
        try:
            text = (d.parent / "CONTRACT_VERSION").read_text()
        except OSError:
            raise ContractVersionMismatch("문진 스키마 CONTRACT_VERSION을 읽을 수 없다") from None
        if violations("SurveyVersionCheckInput", {"contract_version_text": text}):
            raise ContractVersionMismatch(f"문진 스키마 버전을 지원하지 않거나 읽을 수 없다 (지원: {SUPPORTED_RUNTIME_CONTRACT_VERSION})")
        self.contract_version = _version({"contract_version_text": text})["contract_version"]

    def model_output_schema(self, role: str) -> dict:
        """제약 디코딩에 쓸 출력 스키마(완화 전)."""
        return io_validate.output_schema(role)

    def validate_input(self, role: str, value: Any) -> None:
        r = _validate({"role": role, "direction": "input", "value": value})["violations"]
        if r:
            raise ContractViolation(f"{io_validate.INPUT_CONTRACT[role]} 입력 계약 위반", [(v["path"], v["keyword"]) for v in r])

    def output_violations(self, role: str, value: Any) -> list[tuple[str, str]]:
        """출력 계약(ActionExtraction 또는 ResponseText)으로 엄격하게 검증한다. 위반이 없으면 빈 목록."""
        return [(v["path"], v["keyword"]) for v in _validate({"role": role, "direction": "output", "value": value})["violations"]]
