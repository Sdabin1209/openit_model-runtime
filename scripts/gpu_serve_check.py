"""실제 서버(python -m model_runtime.server, 실제 VLLMBackend)를 띄우고, 참조 I4 서버(boundary-contracts/conformance)에 릴리스를 올려
두고 PUT /v1/releases/{id}로 적재한 뒤 parse/generate로 평가 표본을 보내 지연·메모리·보호 개입률을 잰다.
호스트의 venv에서 flock 아래에서 돌린다. 어댑터는 읽기 전용 경로를 가리키기만 한다(복사하지 않음).
사용: gpu_serve_check.py --out DIR --adapters-root RUN_DIR --samples-root RUN_DIR/data --schema-dir S --profile P [--n 50]"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def smi_used():
    return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True).stdout.strip())


def put(url):
    req = urllib.request.Request(url, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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
    root = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(root / "vendor" / "boundary-contracts"), str(root / "src")]
    import vllm
    import model_runtime as mr
    from conformance.servers import ReferenceServer, adapter_digest, ref_of, sealed
    feats = mr.DEFAULT_SUPPORTED_FEATURES
    be = type("B", (), {"name": "vllm", "version": vllm.__version__, "supported_features": frozenset(feats)})()
    cfgs = mr.fake_serving_configs(be)
    for c in cfgs.values():
        c["decoding"]["retry_temperature"] = 0.7   # 재시도는 온도를 올린다(design.md 2.3)
    files = {r: {f.name: f.read_bytes() for f in Path(f"{a.adapters_root}/adapters/{r}").iterdir() if f.is_file()} for r in cfgs}
    ex = json.loads((root / "vendor" / "boundary-contracts" / "examples" / "ReleaseManifest.json").read_text())
    bank = {"version": 1, "questions": []}
    ex.update(release_id="rel-gpu-dev", model_runtime_version=mr.RUNTIME_VERSION, runtime_contract_version=1,
              question_set=ref_of("qs-main", "1", bank))
    for r, c in cfgs.items():
        ex["adapters"][r] = {"adapter_hash": adapter_digest(files[r]), "serving_config": c, "serving_config_hash": mr.serving_config_hash(c)}
    i4 = ReferenceServer({"tok": "reader"}).start()
    i4.releases["rel-gpu-dev"] = {"manifest": sealed(ex), "bank": bank, "adapters": files, "pass": True}
    i4.release_order.append("rel-gpu-dev")
    RID = "rel-gpu-dev"
    env = dict(os.environ, MODEL_RUNTIME_BACKEND="vllm", MODEL_RUNTIME_SCHEMA_DIR=a.schema_dir, MODEL_RUNTIME_PROFILE=a.profile,
               MODEL_RUNTIME_RELEASE_URL=i4.url, MODEL_RUNTIME_RELEASE_TOKEN="tok", MODEL_RUNTIME_BUNDLE_DIR=str(out / "bundles"),
               MODEL_RUNTIME_PUT_WAIT_S="120", MODEL_RUNTIME_QUEUE_WAIT_S="600",
               MODEL_RUNTIME_HOST="127.0.0.1", MODEL_RUNTIME_PORT=str(a.port))
    base = f"http://127.0.0.1:{a.port}"
    before = smi_used()
    log = open(out / "server.log", "w")
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-m", "model_runtime.server"], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
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
        t1 = time.time(); res["put"] = put(f"{base}/v1/releases/{RID}"); res["put_s"] = round(time.time() - t1, 1)
        res["put_again"] = put(f"{base}/v1/releases/{RID}")[0]
        res["gpu_mib_loaded"] = smi_used()
        res["info"] = json.loads(urllib.request.urlopen(base + "/v1/info").read())
        peak = res["gpu_mib_ready"]
        for role, path, name in (("LORA_A", "parse", "eval_samples.jsonl"), ("LORA_B", "generate", "eval_samples.jsonl")):
            rows = [json.loads(l) for l in (Path(a.samples_root) / role / name).read_text(encoding="utf-8").splitlines()[:a.n]]
            ts, outc, atts, prot, exact, ex = [], {}, [], {}, 0, []
            for i, r in enumerate(rows):
                o, h, dt = post(f"{base}/v1/releases/{RID}/{path}", r["input"])
                ts.append(dt); oc = h["X-Openit-Outcome"]; outc[oc] = outc.get(oc, 0) + 1
                atts.append(int(h["X-Openit-Attempts"]))
                for kv in filter(None, h["X-Openit-Protections"].split(",")):
                    k = kv.split(":")[0]; prot[k] = prot.get(k, 0) + 1
                if role == "LORA_A":
                    exact += o == r["expected"]
                if i < 2:
                    ex.append({"input_utterance": r["input"].get("utterance") or r["input"].get("directive", {}).get("action"),
                               "output": o, "outcome": oc, "attempts": h["X-Openit-Attempts"], "seconds": round(dt, 2)})
                peak = max(peak, smi_used())
            ts_s = sorted(ts)
            res[role] = {"n": len(rows), "outcomes": outc, "protections": prot, "attempts_gt1": sum(x > 1 for x in atts),
                         "expected_exact_match": exact if role == "LORA_A" else None,
                         "latency_s": {"first": round(ts[0], 2), "p50": round(ts_s[len(ts_s) // 2], 3), "p95": round(ts_s[int(len(ts_s) * .95) - 1], 3), "max": round(ts_s[-1], 2)},
                         "examples": ex}
        res["gpu_mib_peak"] = peak
        
        try:
            post(f"{base}/v1/releases/nope/parse", {})
        except urllib.error.HTTPError as e:
            res["unloaded_release_status"] = e.code
    finally:
        i4.stop()
        p.terminate()   # 서버가 SIGTERM에서 엔진을 내린다
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)
        res["gpu_mib_after_sigterm"] = smi_used()   # 459 근처면 서버가 엔진을 스스로 내렸다
        try:   # 남은 자식(EngineCore)이 GPU 메모리를 쥐지 않게 프로세스 그룹을 정리한다
            os.killpg(p.pid, 9)
        except ProcessLookupError:
            pass
    (out / "serve_check.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "info"}, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
