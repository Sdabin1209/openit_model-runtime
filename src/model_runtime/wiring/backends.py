"""백엔드 조립: 문항 흐름 파일(flow_file_load)과 규칙표로 모방 백엔드를 만든다. 컴포넌트끼리는 서로를 모르므로 이 연결은 wiring의 몫이다."""
from __future__ import annotations

from ..components.flow_file_load import load_fake_corrections, load_fake_flow, load_fake_flow_spec, load_fake_reasks
from ..components.mimic_backend import MimicBackend, MimicExtractor, MimicResponder, load_rules


def mimic_backend_from_files(flow_file: str | None = None, rules_file: str | None = None) -> MimicBackend:
    """문항 흐름 파일(가짜 백엔드와 같은 형식)과 규칙표 파일(선택)로 MimicBackend를 만든다. 틀리면 OSError/ValueError."""
    rules = load_rules(rules_file)
    if not flow_file:
        return MimicBackend(MimicExtractor(rules=rules), MimicResponder(rules))
    order = load_fake_flow(flow_file)
    slots, skips = load_fake_flow_spec(flow_file)
    return MimicBackend(MimicExtractor(order, slots, skips, load_fake_corrections(flow_file), load_fake_reasks(flow_file), rules), MimicResponder(rules))
