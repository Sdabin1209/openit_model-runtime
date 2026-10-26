"""중립 어댑터(make_neutral_package.py)가 실제 vLLM에서 로딩되고 출력이 기반 모델과 같은지 확인한다(온도 0).
호스트의 venv에서 flock 아래에서 돌린다. 사용: gpu_neutral_check.py PACKAGE_DIR PROFILE"""
import json
import os
import sys
from pathlib import Path

pkg, prof_path = Path(sys.argv[1]), sys.argv[2]
root = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(root / "src")]
import model_runtime as mr  # noqa: E402

def main():
    prof = mr.load_profile(prof_path)
    for k, v in prof.get("env", {}).items():
        if not k.startswith("_"):
            os.environ.setdefault(k, str(v))
    ad = {r: pkg / r / "adapter" for r in ("LORA_A", "LORA_B")}
    b = mr.VLLMBackend(None, profile=prof).bind(ad)
    from vllm import SamplingParams  # noqa: E402
    prompts = ["안녕하세요. 오늘 컨디션이 어떠세요?", "두통이 사흘째 계속돼요. 한 줄로 답해 주세요."]
    sp = SamplingParams(temperature=0, max_tokens=48, seed=1)
    base = [o.outputs[0].text for o in b._llm.generate(prompts, sp, use_tqdm=False)]
    res = {"base": base}
    for r in ad:
        out = [o.outputs[0].text for o in b._llm.generate(prompts, sp, lora_request=b.build_lora_request(r), use_tqdm=False)]
        res[r] = {"same_as_base": out == base, "out": out}
    b.close()
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":   # vLLM이 자식 프로세스를 spawn하므로 가드가 필요하다
    main()
