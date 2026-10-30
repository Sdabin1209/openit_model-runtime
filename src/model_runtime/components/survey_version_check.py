"""survey_version_check 컴포넌트: 문진 스키마 CONTRACT_VERSION 파일의 글자가 이 영역이 지원하는 정수 버전인지 확인한다. SurveyVersionCheckInput -> SurveyVersionCheckResult.

지원하는 값은 1 하나다(brief 3절, D14·D19). 읽을 수 없거나 다른 값은 입력 스키마가 거부한다(wiring이 ContractVersionMismatch로 바꾼다). 파일 읽기는 wiring의 몫이다."""
from __future__ import annotations

from typing import Mapping

INPUT, OUTPUT = "SurveyVersionCheckInput", "SurveyVersionCheckResult"
SUPPORTED_RUNTIME_CONTRACT_VERSION = 1   # 스키마 SurveyVersionCheckInput의 패턴과 같은 값. tests/test_structure.py가 둘이 같음을 본다


def run(req: Mapping) -> dict:
    return {"contract_version": int(req["contract_version_text"].strip())}
