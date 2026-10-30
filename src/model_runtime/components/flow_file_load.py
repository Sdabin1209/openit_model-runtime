"""flow_file_load 컴포넌트: 문항 흐름 파일을 읽고 계약 RuntimeFlowFile로 검증한다. FlowFileRef -> FakeFlowSpec.

형식의 정본은 계약이다(U44). 위반이면 ValueError(값 없이 경로와 키워드만). 옛 코드는 모르는 키를 조용히 무시했지만 계약은 거부한다(`skip` 같은 오타가 사라지지 않게).
가짜·모방 백엔드가 같은 입력 형식(order, slots, skips, corrections, reasks)을 받으므로 읽기를 한 곳에 둔다.
스키마 밖 조건(schemas/README.md의 표): corrections·reasks의 key 문항이 order에 있어야 한다(항목과 order 사이의 대응)."""
from __future__ import annotations

import json

from boundary_contracts import validator

INPUT, OUTPUT = "FlowFileRef", "FakeFlowSpec"


def _read_flow_file(path: str):
    """문항 흐름 파일을 읽고 계약 RuntimeFlowFile로 검증한다(형식의 정본은 계약, U44). 위반이면 ValueError(값 없이 경로와 키워드만).
    옛 코드는 모르는 키를 조용히 무시했지만 계약은 거부한다(`skip` 같은 오타가 사라지지 않게)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    errs = sorted(validator("RuntimeFlowFile").iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        raise ValueError("문항 흐름 파일이 RuntimeFlowFile 계약을 어긴다: " + ", ".join(f"/{'/'.join(map(str, e.absolute_path))}:{e.validator}" for e in errs[:5]))
    return data


def _check_items_in_order(items: list, order, what: str) -> None:
    """key의 문항은 order에 있어야 한다. 스키마가 표현하지 못하는 항목 사이의 대응이라 여기서만 본다."""
    for c in items:
        if isinstance(order, list) and c["key"].rsplit(".", 1)[0].split("[")[0] not in order:
            raise ValueError(f"{what}의 key 문항이 order에 없다")


def run(ref) -> dict:
    """파일 하나를 읽어 가짜·모방 백엔드가 쓰는 모든 부분을 낸다. 목록 형식(`["Q1.D01", ...]`) 파일은 order만 있고 나머지는 비어 있다."""
    data = _read_flow_file(ref["path"])
    if not isinstance(data, dict):
        return {"order": list(data), "slots": {}, "skips": [], "corrections": [], "reasks": []}
    out = {"order": list(data["order"]), "slots": data.get("slots", {}), "skips": list(data.get("skips", [])),
           "corrections": list(data.get("corrections", [])), "reasks": list(data.get("reasks", []))}
    _check_items_in_order(out["corrections"], data.get("order"), "corrections")
    _check_items_in_order(out["reasks"], data.get("order"), "reasks")
    return out


def load_fake_flow(path: str) -> list[str]:
    """문항 ID 목록 JSON(`["Q1.D01", ...]` 또는 `{"order": [...]}`)의 order를 읽는다. 형식이 틀리면 ValueError."""
    return run({"path": path})["order"]


def load_fake_flow_spec(path: str) -> tuple[dict, list]:
    """`slots`({문항ID: [{"key", "type"}]}, 첫 칸만 쓴다)와 `skips`. 목록 형식이거나 항목이 없으면 ({}, [])."""
    r = run({"path": path})
    return r["slots"], r["skips"]


def load_fake_corrections(path: str) -> list:
    """`corrections`(발화 문구 -> 정정 값). 목록 형식이거나 항목이 없으면 []."""
    return run({"path": path})["corrections"]


def load_fake_reasks(path: str) -> list:
    """`reasks`(발화 문구 -> REASK, 서비스의 되묻기 한도 시험용). 목록 형식이거나 항목이 없으면 []."""
    return run({"path": path})["reasks"]
