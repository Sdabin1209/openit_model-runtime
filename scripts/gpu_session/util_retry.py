"""vLLM 기동 실패가 'GPU 메모리를 요구량만큼 못 잡은' 종류인지 판별하고, 재시도용 util을 계산한다(ADR 0016).
사용: util_retry.py classify SERVER_LOG   -> 첫 줄 memory|other, 둘째 줄 근거 한 줄(200자)
      util_retry.py next UTIL            -> UTIL-0.05 (소수 둘째 자리)
      util_retry.py record FILE UTIL OUTCOME [REASON]   -> FILE(JSON 배열)에 시도 한 건을 덧붙인다
재시도 대상은 '요구한 util만큼 메모리를 못 잡음'뿐이다. util을 낮추면 도움이 되는 경우만이다:
  - "Free memory on device ... is less than desired GPU memory utilization" (vllm/v1/worker/gpu_worker.py의 초기화 검사; 다른 프로세스가 GPU를 쓰고 있을 때)
  - "CUDA out of memory" / "OutOfMemoryError" (torch; 프로파일링 중 활성값 최대치가 util 몫을 넘을 때)
재시도하지 않는 것: "No available memory for the cache blocks", "larger than the available KV cache memory" (KV 캐시 몫이 모자란 것이라 util을 낮추면 더 나빠진다.
  vLLM 자신의 안내도 util을 올리거나 max_model_len을 줄이라고 한다 -> 프로파일을 바꿔야 한다), 그 밖의 모든 오류.
첫 문구는 gpu-dev 실기(vLLM 0.31.0, util 0.935)에서 재현해 확인했다. 나머지는 vLLM 소스·보고된 로그 지식에 근거하며 실기 재현은 못 했다(미확인) - 현장에서 문구가 다르면 other로 분류돼 즉시 멈추고 로그가 남는다(안전한 쪽).
"""
import json
import re
import sys
from pathlib import Path

# KV 캐시 부족은 먼저 검사해서 제외한다(같은 로그에 'gpu_memory_utilization'이 함께 나온다)
NOT_RETRYABLE = re.compile(r"No available memory for the cache blocks|larger than the available KV cache memory|KV cache is needed", re.I)
RETRYABLE = re.compile(r"Free memory on device.{0,200}less than desired GPU memory utilization|CUDA out of memory|OutOfMemoryError", re.I | re.S)
STEP = 0.05


def classify(text: str) -> tuple[str, str]:
    if NOT_RETRYABLE.search(text):
        return "other", "KV 캐시 몫 부족(util을 낮춰서는 해결되지 않음)"
    m = RETRYABLE.search(text)
    if m:
        line = next((l for l in text.splitlines() if m.group(0).split("\n")[0][:40] in l), m.group(0))
        return "memory", line.strip()[:200]
    last = [l for l in text.splitlines() if l.strip()][-1:]
    return "other", (last[0].strip()[:200] if last else "로그 없음")


def next_util(u: float) -> float:
    return round(u - STEP, 2)


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "classify":
        kind, why = classify(Path(argv[2]).read_text(encoding="utf-8", errors="replace"))
        print(kind); print(why); return 0
    if len(argv) == 3 and argv[1] == "next":
        print(next_util(float(argv[2]))); return 0
    if len(argv) >= 5 and argv[1] == "record":
        p = Path(argv[2]); cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        cur.append({"util": float(argv[3]), "outcome": argv[4], "reason": argv[5] if len(argv) > 5 else ""})
        p.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"); return 0
    print(__doc__, file=sys.stderr); return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
