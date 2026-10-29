"""첫 실제 백엔드: vLLM(프로세스 안 엔진) + 구조화 출력(JSON 스키마) + LoRA 요청 (history/design-v1.md §2.1).

vLLM은 선택 의존(`pip install .[vllm]`)이고 생성할 때 import한다. 없으면 BackendNotAvailable이다.
상태: 요청 조립은 단위 시험(vLLM 모킹)으로 확인했다. 실제 vLLM에서의 동작은 GPU 시험(scripts/gpu_check.sh, history/design-v1.md §11)
전에는 확인되지 않았다. 12GB 같은 환경별 값은 코드에 두지 않고 프로파일(profiles/*.json)로 받는다."""
from __future__ import annotations

import copy
import importlib
import itertools
import json
from pathlib import Path
from typing import Any, Mapping

from boundary_contracts import validator

from .backends import GenerationRequest
from .errors import BackendNotAvailable, ContractViolation, UnsupportedServingConfig

# gpu-dev 시험(vLLM 0.31.0 + xgrammar 0.2.7, 2026-10-15, history/design-v1.md §11)으로 제약이 지켜지는 것을 확인한 키워드.
# relax-2(0.6.4, D82): pattern·minItems 추가. 2026-10-27 컨테이너 안 직접 시험에서 xgrammar 0.2.7이 pattern을 강제하는 것을 헤드가 확인했다(minItems는 같은 시험에서 확인되었는지 이 리포는 모른다).
# relax-3(0.6.5, D83): maxItems 추가(allOf를 anyOf로 펼칠 때 REDIRECT의 values 빈 목록 규칙에 쓰인다). CPU에서 xgrammar 0.2.7로 강제되는 것을 확인했다(scripts/xgrammar_check.py). 실제 vLLM에서는 미확인.
# 넣지 않은 것: oneOf(const 열거는 enum으로 바꿔 쓴다. 객체 가지는 표본이 적어 anyOf로 넓힌다), allOf·if/then/not(조용히 무시됨),
# uniqueItems(오류로 거부됨). 조용히 무시되는 키워드는 넣으면 안 된다(완화되지 않은 채 제약이 느슨해진다).
DEFAULT_SUPPORTED_FEATURES = ("type", "properties", "required", "enum", "const", "items", "anyOf", "$defs", "$ref",
                              "additionalProperties", "minLength", "minimum", "pattern", "minItems", "maxItems")
ROLES = ("LORA_A", "LORA_B")

def load_profile(path: Path | str) -> dict:
    """프로파일 JSON을 읽는다. 형식은 계약 RuntimeProfile이 정한다(알려진 키만, 주석은 `_` 키). 위반이면 ContractViolation(값 없이 경로와 키워드만)."""
    try:
        p = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ContractViolation("프로파일을 읽을 수 없다") from None
    errs = sorted(validator("RuntimeProfile").iter_errors(p), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        raise ContractViolation("프로파일 형식 위반", [("/" + "/".join(map(str, e.absolute_path)), e.validator) for e in errs])
    return p


def read_adapter_config(role: str, adapter_dir: Path) -> dict:
    """PEFT 어댑터 디렉터리의 adapter_config.json을 읽는다. 형식이 아니면 ContractViolation(원문 없이 역할만)."""
    try:
        cfg = json.loads((Path(adapter_dir) / "adapter_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ContractViolation(f"{role} 어댑터가 PEFT 형식이 아니다(adapter_config.json을 읽을 수 없다)") from None
    r = cfg.get("r") if isinstance(cfg, dict) else None
    if not isinstance(r, int) or isinstance(r, bool) or r < 1:
        raise ContractViolation(f"{role} 어댑터 설정에 순위 r이 없다")
    return cfg


class VLLMBackend:
    name = "vllm"

    _next_lora_id = itertools.count(1)   # vLLM의 lora_int_id는 어댑터마다 서버 안에서 유일해야 한다

    def __init__(self, adapters: Mapping[str, Path] | None = None, *, profile: Mapping[str, Any] | None = None,
                 supported_features=DEFAULT_SUPPORTED_FEATURES, base_model: str | None = None,
                 _import=importlib.import_module):
        """adapters: 역할 → 어댑터 디렉터리. None이면 엔진만 올리고(base_model 또는 프로파일 필요) 어댑터는 bind()로 묶는다. 두 어댑터를 한 기반 모델에 올린다.
        base_model: 없으면 profile["base_model"], 없으면 어댑터의 adapter_config.json에서 읽는다(두 어댑터가 같아야 한다)."""
        try:
            vllm = _import("vllm")
            sp_mod = _import("vllm.sampling_params")
            lora_mod = _import("vllm.lora.request")
        except ImportError:
            raise BackendNotAvailable("vLLM이 설치되어 있지 않다 (pip install .[vllm])") from None
        profile = dict(profile or {})
        self._max_rank = int(dict(profile.get("engine", {})).get("max_lora_rank", 16))
        model = base_model or profile.get("base_model")
        if adapters is not None and not model:
            cfgs0 = self._check_adapters(adapters, None)
            model = cfgs0["LORA_A"].get("base_model_name_or_path")
        if not model:
            raise ContractViolation("기반 모델을 알 수 없다(base_model, 프로파일, LORA_A 어댑터 설정 중 하나가 필요하다)")
        self.base_model = model
        self._adapters = {}
        if adapters is not None:
            self._adapters = self._ids(self._check_adapters(adapters, model) and adapters)
        self.supported_features = frozenset(supported_features)
        self.version = str(getattr(vllm, "__version__", "unknown"))
        self._sp = sp_mod
        self._LoRARequest = lora_mod.LoRARequest
        engine = dict(profile.get("engine", {}))
        engine.setdefault("enable_lora", True)
        engine.setdefault("max_loras", len(ROLES))
        engine.setdefault("max_lora_rank", self._max_rank)
        engine.setdefault("disable_log_stats", True)   # 프롬프트·출력 로그 방지(§11-11)는 GPU 시험에서 확인
        self._structured_new = hasattr(sp_mod, "StructuredOutputsParams")
        # 구조화 출력 엔진 인자 이름이 vLLM 버전마다 다르다(guided_decoding_backend → structured_outputs_config). 확인 전.
        be = engine.pop("structured_outputs_backend", None) or engine.pop("guided_decoding_backend", None)
        if be:
            engine["guided_decoding_backend"] = be
        self.engine_args = engine
        self._llm = vllm.LLM(model=model, **engine)

    def close(self) -> None:
        """엔진 프로세스(EngineCore)를 내린다. 종료 시그널을 받았을 때 서버가 부른다. 실패해도 예외를 내지 않는다."""
        llm = getattr(self, "_llm", None)
        try:
            llm.llm_engine.engine_core.shutdown()   # 속성 이름은 vLLM 0.31.0에서 확인한 것. 다른 버전이면 __del__에 맡긴다
        except Exception:   # noqa: BLE001
            pass

    def _check_adapters(self, adapters, model):
        """엔진에 올리기 전 사전 검사. 읽은 어댑터 설정을 돌려준다."""
        if set(adapters) != set(ROLES):
            raise ContractViolation("adapters의 역할 집합이 LORA_A, LORA_B와 다르다")
        cfgs = {r: read_adapter_config(r, Path(adapters[r])) for r in ROLES}
        for r in ROLES:
            if cfgs[r]["r"] > self._max_rank:
                raise UnsupportedServingConfig(f"{r} 어댑터 순위가 max_lora_rank({self._max_rank})를 넘는다")
            if model is not None and cfgs[r].get("base_model_name_or_path") != model:
                raise UnsupportedServingConfig(f"{r} 어댑터의 기반 모델이 사용할 기반 모델과 다르다")
        return cfgs

    def _ids(self, adapters):
        return {r: (next(VLLMBackend._next_lora_id), str(Path(adapters[r]))) for r in ROLES}

    def bind(self, adapters: Mapping[str, Path]) -> "VLLMBackend":
        """같은 엔진(기반 모델 1개)에 다른 어댑터 쌍을 묶은 백엔드. 사전 검사를 통과해야 한다. 엔진은 새로 만들지 않는다."""
        self._check_adapters(adapters, self.base_model)
        b = copy.copy(self)
        b._adapters = self._ids(adapters)
        return b

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
            raise ContractViolation("어댑터가 묶이지 않았거나 알 수 없는 역할")
        int_id, path = self._adapters[role]
        return self._LoRARequest(role, int_id, path)

    def generate(self, request: GenerationRequest) -> str:
        outs = self._llm.generate([request.prompt], self.build_sampling_params(request),
                                  lora_request=self.build_lora_request(request.role), use_tqdm=False)
        # finish_reason == "length"이면 JSON이 닫히지 않았을 수 있다. 그대로 돌려주면 세션의 검증이 위반으로 잡는다(§11-7).
        return outs[0].outputs[0].text
