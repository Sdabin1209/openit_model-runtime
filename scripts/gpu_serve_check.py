"""실제 서버(python -m model_runtime.server, 실제 VLLMBackend)를 띄워 HTTP로 평가 표본을 보내고 지연·메모리·보호 개입률을 잰다.
호스트의 venv에서 flock 아래에서 돌린다. 어댑터는 읽기 전용 경로를 가리키기만 한다(복사하지 않음).
사용: gpu_serve_check.py --out DIR --adapters-root RUN_DIR --samples-root RUN_DIR/data --schema-dir S --profile P [--n 50]"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def smi_used():
    return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True).stdout.strip())


def post(url, body):
    t = time.time()
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read()), dict(r.headers), time.time() - t


def main():
    ap = argparse.ArgumentParser()
    for a in ("out", "adapters_root", "samples_root", "schema_dir", "profile"):
        ap.add_argument("--" + a.replace("_", "-"), required=True)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--port", type=int, default=18081)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MODEL_RUNTIME_BACKEND="vllm", MODEL_RUNTIME_SCHEMA_DIR=a.schema_dir, MODEL_RUNTIME_PROFILE=a.profile,
               MODEL_RUNTIME_ADAPTER_LORA_A=f"{a.adapters_root}/adapters/LORA_A", MODEL_RUNTIME_ADAPTER_LORA_B=f"{a.adapters_root}/adapters/LORA_B",
               MODEL_RUNTIME_HOST="127.0.0.1", MODEL_RUNTIME_PORT=str(a.port))
    base = f"http://127.0.0.1:{a.port}"
    before = smi_used()
    log = open(out / "server.log", "w")
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-m", "model_runtime.server"], env=env, stdout=log, stderr=subprocess.STDOUT)
    res = {"gpu_mib_before": before}
    try:
        while True:
            if p.poll() is not None:
                raise SystemExit("서버가 종료됨 (server.log)")
            try:
                urllib.request.urlopen(base + "/readyz", timeout=2); break
            except Exception:   # noqa: BLE001
                time.sleep(2)
            if time.time() - t0 > 600:
                raise SystemExit("준비 시간 초과")
        res["ready_s"] = round(time.time() - t0, 1)
        res["gpu_mib_ready"] = smi_used()
        res["info"] = json.loads(urllib.request.urlopen(base + "/v1/info").read())
        peak = res["gpu_mib_ready"]
        for role, path, name in (("LORA_A", "parse", "eval_samples.jsonl"), ("LORA_B", "generate", "eval_samples.jsonl")):
            rows = [json.loads(l) for l in (Path(a.samples_root) / role / name).read_text(encoding="utf-8").splitlines()[:a.n]]
            ts, outc, atts, prot, exact, ex = [], {}, [], {}, 0, []
            for i, r in enumerate(rows):
                o, h, dt = post(f"{base}/v1/{path}", r["input"])
                ts.append(dt); oc = h["X-Model-Runtime-Outcome"]; outc[oc] = outc.get(oc, 0) + 1
                atts.append(int(h["X-Model-Runtime-Attempts"]))
                for kv in filter(None, h["X-Model-Runtime-Protections"].split(",")):
                    k = kv.split(":")[0]; prot[k] = prot.get(k, 0) + 1
                if role == "LORA_A":
                    exact += o == r["expected"]
                if i < 2:
                    ex.append({"input_utterance": r["input"].get("utterance") or r["input"].get("directive", {}).get("action"),
                               "output": o, "outcome": oc, "attempts": h["X-Model-Runtime-Attempts"], "seconds": round(dt, 2)})
                peak = max(peak, smi_used())
            ts_s = sorted(ts)
            res[role] = {"n": len(rows), "outcomes": outc, "protections": prot, "attempts_gt1": sum(x > 1 for x in atts),
                         "expected_exact_match": exact if role == "LORA_A" else None,
                         "latency_s": {"first": round(ts[0], 2), "p50": round(ts_s[len(ts_s) // 2], 3), "p95": round(ts_s[int(len(ts_s) * .95) - 1], 3), "max": round(ts_s[-1], 2)},
                         "examples": ex}
        res["gpu_mib_peak"] = peak
        bad = post(base + "/v1/parse", {"utterance": 1}) if False else None
    finally:
        p.terminate()
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
    (out / "serve_check.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "info"}, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
