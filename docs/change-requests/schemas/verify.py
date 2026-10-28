"""CR-006 스키마 초안을 jsonschema로 검증한다. 사용: .venv/bin/python docs/change-requests/schemas/verify.py
각 스키마에 대해 유효 예시는 통과, 거부 예시는 실패해야 한다. ReleaseLoadStatus는 error 안의 BoundaryError를 계약 검증기로도 확인한다."""
import json
import sys
from pathlib import Path

import jsonschema
from boundary_contracts import validator

HERE = Path(__file__).parent
NAMES = ["ServingConfig", "RuntimeInfo", "ReleaseLoadStatus", "RuntimeProfile", "RuntimeFlowFile"]
bad = 0
for n in NAMES:
    schema = json.loads((HERE / f"{n}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema)
    ok = json.loads((HERE / "examples" / f"{n}.valid.json").read_text(encoding="utf-8"))
    no = json.loads((HERE / "examples" / f"{n}.invalid.json").read_text(encoding="utf-8"))
    e_ok, e_no = list(v.iter_errors(ok)), list(v.iter_errors(no))
    print(f"{n}: valid 예시 위반 {len(e_ok)}건, invalid 예시 위반 {len(e_no)}건" + (f" ({e_no[0].message[:70]})" if e_no else ""))
    bad += bool(e_ok) + (not e_no)
from boundary_contracts.errors import BoundaryError   # 실제 코드가 내는 레코드를 쓴다(손으로 쓴 모양이 아니다)
fail = {"release_id": "r1", "state": "FAILED", "error": BoundaryError("FAILED_PRECONDITION", "설정 불일치").to_record()}
v = jsonschema.Draft202012Validator(json.loads((HERE / "ReleaseLoadStatus.schema.json").read_text(encoding="utf-8")))
errs = list(v.iter_errors(fail)) + list(validator("BoundaryError").iter_errors(fail["error"]))
print(f"ReleaseLoadStatus FAILED 예시(error는 BoundaryError 검증기 포함): 위반 {len(errs)}건")
bad += bool(errs)
sys.exit(1 if bad else 0)
