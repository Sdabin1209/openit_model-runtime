#!/usr/bin/env python3
"""서빙의 채팅 조립(prompts._qwen3_nothink)이 Qwen3 tokenizer_config.json의 chat_template 렌더 결과와 같은지 확인한다.

사용: python scripts/check_chat_template.py <tokenizer_config.json>   (jinja2 필요; 이 리포의 의존이 아니라 점검용)
transformers의 apply_chat_template과 같은 방식(ImmutableSandboxedEnvironment, trim_blocks, lstrip_blocks, enable_thinking=False)으로
템플릿을 직접 렌더한다. transformers 자체를 실행한 것은 아니다. 토크나이저(토큰 ID) 단위 대조는 하지 않는다.
종료 코드: 같음 0, 다름 1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from model_runtime import prompts  # noqa: E402

CASES = [("한 줄 시스템", '{"a": 1}'), ("여러 줄\n시스템 `{}` {{x}}", '{"한글": "값", "b": [1, 2]}'), ("끝에 공백 ", "u\n끝")]


def main() -> int:
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)

    def raise_exception(m):
        raise ValueError(m)
    env.globals["raise_exception"] = raise_exception
    env.filters["tojson"] = lambda x, **k: json.dumps(x, ensure_ascii=False)
    tpl = env.from_string(cfg["chat_template"])
    bad = 0
    for system, user in CASES:
        want = tpl.render(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                          add_generation_prompt=True, enable_thinking=False)
        got = prompts._qwen3_nothink(system, user)
        ok = want == got
        bad += not ok
        print("일치" if ok else f"불일치(위치 {next((i for i, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))})")
    print("결과:", "모두 일치" if not bad else "불일치 있음")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
