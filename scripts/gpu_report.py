"""runs/<id>/의 결과를 요약해 출력한다 (사용: gpu_report.py runs/<id>)."""
import json
import re
import sys
from pathlib import Path

d = Path(sys.argv[1])
r = {t: json.loads((d / f"{t}.json").read_text()) for t in ("run1", "run2", "baseline") if (d / f"{t}.json").exists()}
for t, v in r.items():
    print(f"== {t} versions={v.get('versions')} gpu_after_load={v.get('gpu_after_load')}")
    for k, c in v.get("checks", {}).items():
        print(f"  {k}: {'ok' if c['ok'] else 'FAIL'} {json.dumps(c.get('data', c.get('error')), ensure_ascii=False)[:600]}")
if "run1" in r and "run2" in r:
    a = r["run1"]["checks"].get("9_determinism_outputs", {}).get("data")
    b = r["run2"]["checks"].get("9_determinism_outputs", {}).get("data")
    print("9 결정성(서버 두 번 띄움):", "동일" if a is not None and a == b else "다름 또는 없음")
for log in sorted(d.glob("vllm-*.log")):
    txt = log.read_text(errors="replace")
    print(log.name, re.findall(r"Model loading took [\d.]+ \w+", txt)[:2], re.findall(r"[Mm]aximum concurrency[^\n]*", txt)[:1],
          "프롬프트 표식 로그 노출:", "PRIVACYMARK7731" in txt)
