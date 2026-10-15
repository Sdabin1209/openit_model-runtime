"""첫 실제 백엔드: vLLM(프로세스 안 엔진) + 구조화 출력(JSON 스키마) + LoRA 요청 (design.md §2.1).

vLLM은 선택 의존(`pip install .[vllm]`)이고 생성할 때 import한다. 없으면 BackendNotAvailable이다.
상태: 요청 조립은 단위 시험(vLLM 모킹)으로 확인했다. 실제 vLLM에서의 동작은 GPU 시험(scripts/gpu_check.sh, design.md §11)
전에는 확인되지 않았다. 12GB 같은 환경별 값은 코드에 두지 않고 프로파일(profiles/*.json)로 받는다."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from .backends import GenerationRequest
from .errors import BackendNotAvailable, ContractViolation

# gpu-dev 시험(vLLM 0.31.0 + xgrammar 0.2.7, 2026-10-15, design.md §11)으로 제약이 지켜지는 것을 확인한 키워드.
# 넣지 않은 것: oneOf(const 열거는 enum으로 바꿔 쓴다. 객체 가지는 표본이 적어 anyOf로 넓힌다), allOf·if/then/not(조용히 무시됨),
# uniqueItems(오류로 거부됨). 조용히 무시되는 키워드는 넣으면 안 된다(완화되지 않은 채 제약이 느슨해진다).
DEFAULT_SUPPORTED_FEATURES = ("type", "properties", "required", "enum", "const", "items", "anyOf", "$defs", "$ref",
                              "additionalProperties", "minLength", "minimum")
ROLES = ("LORA_A", "LORA_B")

_ENGINE_KEYS = ("dtype", "max_model_len", "gpu_memory_utilization", "max_num_seqs", "max_loras", "max_lora_rank",
                "enforce_eager", "quantization", "guided_decoding_backend", "structured_outputs_backend",
                "disable_log_stats", "tensor_parallel_size")


def load_profile(path: Path | str) -> dict:
    """프로파일 JSON을 읽는다. 키: base_model(선택), engine(vLLM 엔진 인자), 그 밖의 키는 주석으로 무시한다."""
    try:
        p = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ContractViolation("프로파일을 읽을 수 없다") from None
    if not isinstance(p, dict) or not isinstance(p.get("engine", {}), dict):
        raise ContractViolation("프로파일 형식 위반")
    bad = set(p.get("engine", {})) - set(_ENGINE_KEYS)
    if bad:
        raise ContractViolation(f"프로파일 engine에 알 수 없는 키: {sorted(bad)}")
    return p


def _base_model_of(adapter_dir: Path) -> str | None:
    try:
        return json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8")).get("base_model_name_or_path")
    except (OSError, ValueError):
        return None


class VLLMBackend:
    name = "vllm"

    def __init__(self, adapters: Mapping[str, Path], *, profile: Mapping[str, Any] | None = None,
                 supported_features=DEFAULT_SUPPORTED_FEATURES, base_model: str | None = None,
                 _import=importlib.import_module):
        """adapters: 역할 → 어댑터 디렉터리. 두 어댑터를 한 기반 모델에 올린다.
        base_model: 없으면 profile["base_model"], 없으면 어댑터의 adapter_config.json에서 읽는다(두 어댑터가 같아야 한다)."""
        if set(adapters) != set(ROLES):
            raise ContractViolation("adapters의 역할 집합이 LORA_A, LORA_B와 다르다")
        try:
            vllm = _import("vllm")
            sp_mod = _import("vllm.sampling_params")
            lora_mod = _import("vllm.lora.request")
        except ImportError:
            raise BackendNotAvailable("vLLM이 설치되어 있지 않다 (pip install .[vllm])") from None
        profile = dict(profile or {})
        dirs = {r: Path(p) for r, p in adapters.items()}
        bases = {_base_model_of(d) for d in dirs.values()}
        model = base_model or profile.get("base_model") or (bases.pop() if len(bases) == 1 else None)
        if not model:
            raise ContractViolation("기반 모델을 알 수 없다(base_model, 프로파일, 어댑터 설정 중 하나가 필요하다)")
        self.supported_features = frozenset(supported_features)
        self.version = str(getattr(vllm, "__version__", "unknown"))
        self.base_model = model
        self._sp = sp_mod
        self._LoRARequest = lora_mod.LoRARequest
        self._adapters = {r: (i + 1, str(d)) for i, (r, d) in enumerate(sorted(dirs.items()))}
        engine = dict(profile.get("engine", {}))
        engine.setdefault("enable_lora", True)
        engine.setdefault("max_loras", len(ROLES))
        engine.setdefault("disable_log_stats", True)   # 프롬프트·출력 로그 방지(§11-11)는 GPU 시험에서 확인
        self._structured_new = hasattr(sp_mod, "StructuredOutputsParams")
        # 구조화 출력 엔진 인자 이름이 vLLM 버전마다 다르다(guided_decoding_backend → structured_outputs_config). 확인 전.
        be = engine.pop("structured_outputs_backend", None) or engine.pop("guided_decoding_backend", None)
        if be:
            engine["guided_decoding_backend"] = be
        self.engine_args = engine
        self._llm = vllm.LLM(model=model, **engine)

    # --- 요청 조립(단위 시험 대상) ---
    def build_sampling_params(self, request: GenerationRequest):
        d = request.decoding
        kw: dict[str, Any] = {"temperature": d["temperature"], "max_tokens": d["max_tokens"], "seed": request.seed}
        if "top_p" in d:
            kw["top_p"] = d["top_p"]
        if request.constraint_schema is not None:
            schema = json.dumps(request.constraint_schema, ensure_ascii=False)
            if self._structured_new:
                kw["structured_outputs"] = self._sp.StructuredOutputsParams(json=schema)
            else:
                kw["guided_decoding"] = self._sp.GuidedDecodingParams(json=schema)
        return self._sp.SamplingParams(**kw)

    def build_lora_request(self, role: str):
        if role not in self._adapters:
            raise ContractViolation("알 수 없는 역할")
        int_id, path = self._adapters[role]
        return self._LoRARequest(role, int_id, path)

    def generate(self, request: GenerationRequest) -> str:
        outs = self._llm.generate([request.prompt], self.build_sampling_params(request),
                                  lora_request=self.build_lora_request(request.role), use_tqdm=False)
        # finish_reason == "length"이면 JSON이 닫히지 않았을 수 있다. 그대로 돌려주면 세션의 검증이 위반으로 잡는다(§11-7).
        return outs[0].outputs[0].text
