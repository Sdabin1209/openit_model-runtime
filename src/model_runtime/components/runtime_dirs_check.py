"""runtime_dirs_check 컴포넌트: 시작 때 번들 디렉터리·모델 캐시·런타임 캐시 디렉터리를 만들고 쓸 수 있는지 확인한다. RuntimeDirsCheckInput -> RuntimeDirsCheckResult.

볼륨을 처음 마운트해도 동작해야 한다. 이전 실행이 중단되며 남긴 적재 임시 폴더(.bundle-*)는 지운다. 못 쓰면 첫 사유를 낸다(경로와 uid만, 비밀 없음).
스키마 밖 조건(schemas/README.md의 표): 디렉터리 쓰기 가능 여부(파일 시스템 상태)."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

INPUT, OUTPUT = "RuntimeDirsCheckInput", "RuntimeDirsCheckResult"


def _writable(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=d):
        pass


def run(req: Mapping) -> dict:
    bundle = Path(req["bundle_dir"])
    try:
        _writable(bundle)
        for d in bundle.glob(".bundle-*"):   # 지금은 적재 중인 것이 없다
            shutil.rmtree(d, ignore_errors=True)
    except OSError as e:
        return {"error": f"번들 디렉터리 {bundle}에 쓸 수 없다({type(e).__name__}). 볼륨 소유자를 uid {os.getuid()}로 하거나 MODEL_RUNTIME_BUNDLE_DIR를 바꾼다"}
    if req["hf_home"]:   # 이미지가 준비한 경로가 아닌 곳에 볼륨을 마운트하면 root 소유라 실패한다
        try:
            _writable(Path(req["hf_home"]))
        except OSError:
            return {"error": f"hf cache not writable: {req['hf_home']} (uid {os.getuid()})"}
    for d in req["runtime_dirs"]:   # HOME·XDG/vLLM/torch/triton 캐시. 볼륨 마운트가 이미지의 디렉터리를 가린다
        try:
            _writable(Path(d))
        except OSError:
            return {"error": f"runtime dir not writable: {d} (uid {os.getuid()})"}
    return {"error": None}
