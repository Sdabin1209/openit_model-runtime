"""retry_loop 컴포넌트: 공용 재시도 반복. AttemptSpec -> AttemptsResult.

`max_attempts`번까지, 시도마다 시드를 1씩 올리고 재시도 온도를 쓴다(§2.3). parse(raw) -> (값, 파싱 오류 목록), check(값) -> 위반 목록.
왜 한 곳: 시도 횟수·시드·온도 규칙은 두 역할(LoRA-A/B)이 같은 계약이라 따로 두면 어긋난다(ADR 0007, 0017). 역할 컴포넌트끼리는 서로를 모르므로 wiring이 이것을 둘에게 주입한다.
deadline(time.monotonic 기준)이 있으면 재시도는 남은 예산이 직전 시도에 걸린 시간 이상일 때만 한다(계약 '시간 상한 사슬' 2.2.0, ADR 0020).
왜 직전 시도 시간인가: 호출 상한 자체와 비교하면 호출 안에서는 재시도가 영영 안 된다. 직전 시도 시간은 다음 시도가 걸릴 시간의 유일한 관측값이다(추정, 측정 안 함).
바깥 자원(백엔드 generate, 시계)은 인자로 받는다(DESIGN.md 2절)."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from ..types.GenerationRequest import GenerationRequest

INPUT, OUTPUT = "AttemptSpec", "AttemptsResult"


def run(spec: Mapping, *, generate: Callable[[GenerationRequest], str], parse: Callable[[Any], tuple[Any, list]],
        check: Callable[[Any], list], clock: Callable[[], float]) -> dict:   # clock: 호출자가 준다(wiring이 실제 시계, 시험은 가짜 시계, ADR 0029)
    dec = spec["decoding"]
    deadline = spec["deadline"]
    value: Any = None
    errs: list = []
    n = 0
    last_s = 0.0
    for k in range(spec["max_attempts"]):
        if k > 0 and deadline is not None and deadline - clock() < last_s:
            break   # 남은 예산이 모자라면 재시도하지 않고 마지막 결과로 실패 처리한다
        n = k + 1
        t0 = clock()
        d = dec
        if k > 0 and "retry_temperature" in dec:   # 재시도 온도. 시도 0은 항상 temperature
            d = {**dec, "temperature": dec["retry_temperature"]}
        raw = generate(GenerationRequest(spec["role"], spec["prompt"], spec["constraint_schema"], d, dec["seed"] + k, k, spec["input"]))
        last_s = clock() - t0
        value, perr = parse(raw)
        if perr:
            errs = perr
            continue
        errs = check(value)
        if not errs:
            break
    return {"output": value, "errors": list(errs), "attempts": n}
