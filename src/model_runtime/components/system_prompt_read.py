"""system_prompt_read 컴포넌트: 어댑터 디렉터리 안의 시스템 프롬프트 파일을 읽는다. SystemPromptReadInput -> SystemPromptText (D75).

ref는 SystemPromptRef({path, sha256}). 파일이 없거나 디렉터리 밖을 가리키거나 해시(canonical.file_hash)가 다르면 SystemPromptMismatch.
스키마 밖 조건(schemas/README.md의 표): 파일 존재, 디렉터리 밖 경로 금지, 해시 일치. 파일 시스템 상태와 파일 내용에 달려 있어 스키마로 쓸 수 없다."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from boundary_contracts.canonical import file_hash

from ..wiring.errors import SystemPromptMismatch

INPUT, OUTPUT = "SystemPromptReadInput", "SystemPromptText"


def run(req: Mapping) -> str:
    role, adapter_dir, ref = req["role"], req["adapter_dir"], req["ref"]
    try:
        base = Path(adapter_dir).resolve()
        f = base.joinpath(*str(ref["path"]).split("/")).resolve()
        if not f.is_relative_to(base) or not f.is_file() or file_hash(f) != ref["sha256"]:
            raise SystemPromptMismatch(role)
        text = f.read_bytes().decode("utf-8")
    except (KeyError, TypeError, OSError, UnicodeDecodeError):
        raise SystemPromptMismatch(role) from None
    return text
