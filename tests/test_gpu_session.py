"""GPU 세션 키트(scripts/gpu_session) 로컬 시험: GPU 없이 가짜 백엔드와 가짜 nvidia-smi로 preflight, prepare_bundle, measure(run_session 경유)를 확인한다."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import A_INPUT, B_INPUT, GOOD_A, GOOD_B

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "scripts" / "gpu_session"
sys.path.insert(0, str(KIT))
import measure  # noqa: E402


def sh(path, name, body):
    p = path / name
    p.write_text("#!/bin/sh\n" + body + "\n"); p.chmod(0o755)
    return str(p)


def smi(tmp_path, used, apps=""):
    return sh(tmp_path, "smi", f'case "$*" in *query-compute-apps*) echo "{apps}";; *) echo "0, {used}, 100000";; esac')


def preflight(smi_cmd, *args):
    return subprocess.run(["bash", str(KIT / "preflight.sh"), *args], capture_output=True, text=True, env={**os.environ, "NVIDIA_SMI": smi_cmd})


def test_preflight_idle_passes(tmp_path):
    r = preflight(smi(tmp_path, 300), "--until", "23:59") if _before("23:59") else preflight(smi(tmp_path, 300))
    assert r.returncode == 0 and "PREFLIGHT=OK" in r.stdout


def _before(hhmm):
    import datetime
    return datetime.datetime.now().strftime("%H:%M") < hhmm


def test_preflight_busy_by_memory_reports_and_refuses(tmp_path):
    r = preflight(smi(tmp_path, 91000))
    assert r.returncode == 2 and "PREFLIGHT=BUSY" in r.stdout and "91000" in r.stdout


def test_preflight_lists_foreign_process(tmp_path):
    r = preflight(smi(tmp_path, 100, apps="4242, vllm_worker, 512"))
    assert r.returncode == 2 and "pid=4242" in r.stdout and "vllm_worker" in r.stdout and "512MiB" in r.stdout


def test_preflight_threshold_is_configurable(tmp_path):
    assert preflight(smi(tmp_path, 3000), "--max-used-mib", "4000").returncode == 0
    assert preflight(smi(tmp_path, 3000)).returncode == 2


def test_preflight_min_free(tmp_path):
    r = preflight(smi(tmp_path, 300), "--min-free-mib", "99900")
    assert r.returncode == 2 and "필요량" in r.stdout


def test_preflight_past_deadline_refuses(tmp_path):
    r = preflight(smi(tmp_path, 300), "--until", "00:00")
    assert r.returncode == 2 and "PREFLIGHT=TIME" in r.stdout


def test_preflight_without_nvidia_smi_fails_clearly(tmp_path):
    r = preflight("/nonexistent/nvidia-smi")
    assert r.returncode == 3 and "PREFLIGHT=NO_SMI" in r.stdout


def test_preflight_bad_args(tmp_path):
    assert preflight(smi(tmp_path, 1), "--until", "25:99").returncode == 64
    assert preflight(smi(tmp_path, 1), "--max-used-mib", "x").returncode == 64


def write_c1(path, role, n, system="SYS"):
    rows = []
    for i in range(n):
        user = A_INPUT if role == "a" else B_INPUT
        gold = GOOD_A if role == "a" else GOOD_B
        rows.append({"human_review": "auto_only", "scenario_id": f"s{i}", "split": "val", "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}, {"role": "assistant", "content": gold}]})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def make_adapter(d, base="Qwen/Qwen3-8B", r=16):
    d.mkdir(parents=True)
    (d / "adapter_config.json").write_text(json.dumps({"r": r, "base_model_name_or_path": base, "target_modules": ["q_proj"], "lora_alpha": 32}))
    (d / "adapter_model.safetensors").write_bytes(b"\0" * 64)
    (d / "README.md").write_text("not copied")


def run_prepare(tmp_path, *extra):
    return subprocess.run([sys.executable, str(KIT / "prepare_bundle.py"), "--adapter-a", str(tmp_path / "aa"), "--adapter-b", str(tmp_path / "ab"),
                           "--c1-a", str(tmp_path / "a.jsonl"), "--c1-b", str(tmp_path / "b.jsonl"), "--out", str(tmp_path / "out"),
                           "--profile", str(ROOT / "profiles" / "gpu-shared-8b.json"), "--backend", "vllm", "--vllm-version", "0.31.0", *extra],
                          capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})


def setup_inputs(tmp_path, base="Qwen/Qwen3-8B", r=16):
    make_adapter(tmp_path / "aa", base, r); make_adapter(tmp_path / "ab", base, r)
    write_c1(tmp_path / "a.jsonl", "a", 3, "프롬프트 A"); write_c1(tmp_path / "b.jsonl", "b", 3, "프롬프트 B")


def test_prepare_bundle_builds_valid_release(tmp_path):
    from boundary_contracts import validator
    from boundary_contracts.canonical import content_hash, directory_hash
    setup_inputs(tmp_path)
    r = run_prepare(tmp_path)
    assert r.returncode == 0, r.stderr
    rel = tmp_path / "out" / "gpu-session"
    m = json.loads((rel / "manifest.json").read_text(encoding="utf-8"))
    assert not list(validator("ReleaseManifest").iter_errors(m))
    body = {k: v for k, v in m.items() if k != "content_hash"}
    assert m["content_hash"] == content_hash(m)
    for role, text in (("LORA_A", "프롬프트 A"), ("LORA_B", "프롬프트 B")):
        d = rel / "adapters" / role
        assert sorted(p.name for p in d.iterdir()) == ["adapter_config.json", "adapter_model.safetensors", "system_prompt.txt"]   # README는 복사하지 않는다
        assert (d / "system_prompt.txt").read_bytes() == text.encode()
        assert m["adapters"][role]["adapter_hash"] == directory_hash(d)
        assert m["adapters"][role]["serving_config"]["constraint"]["backend_version"] == "0.31.0"
    assert m["adapters"]["LORA_B"]["serving_config"]["decoding"]["constraint_mode"] == "none"
    rep = json.loads((tmp_path / "out" / "PREPARE_REPORT.json").read_text(encoding="utf-8"))
    assert rep["roles"]["LORA_A"]["system_prompt_kinds"] == 1 and rep["warnings"] == []
    assert body


def test_prepare_bundle_reports_multiple_system_prompts(tmp_path):
    setup_inputs(tmp_path)
    write_c1(tmp_path / "x.jsonl", "a", 1, "다른 프롬프트")
    (tmp_path / "a.jsonl").write_text((tmp_path / "a.jsonl").read_text(encoding="utf-8") + (tmp_path / "x.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    r = run_prepare(tmp_path)
    assert r.returncode == 0 and "2종" in r.stderr
    assert (tmp_path / "out/gpu-session/adapters/LORA_A/system_prompt.txt").read_bytes() == "프롬프트 A".encode()   # 첫 행


def test_prepare_bundle_refuses_base_model_mismatch_unless_rewritten(tmp_path):
    setup_inputs(tmp_path, base="/data/models/qwen3-8b")
    r = run_prepare(tmp_path)
    assert r.returncode != 0 and "base_model_name_or_path" in r.stderr
    assert run_prepare(tmp_path, "--rewrite-base-name", "--overwrite").returncode == 0
    cfg = json.loads((tmp_path / "out/gpu-session/adapters/LORA_A/adapter_config.json").read_text())
    assert cfg["base_model_name_or_path"] == "Qwen/Qwen3-8B"
    assert json.loads((tmp_path / "aa/adapter_config.json").read_text())["base_model_name_or_path"] == "/data/models/qwen3-8b"   # 원본은 그대로


def test_prepare_bundle_refuses_rank_over_profile(tmp_path):
    setup_inputs(tmp_path, r=64)
    r = run_prepare(tmp_path)
    assert r.returncode != 0 and "max_lora_rank" in r.stderr


def test_percentile_nearest_rank():
    v = sorted(range(1, 101))
    assert measure.pct(v, .5) == 50 and measure.pct(v, .95) == 95 and measure.pct([], .5) is None and measure.pct([3.0], .95) == 3.0


def test_summarize_separates_cold_retry_and_failures():
    def rec(lat, status=200, attempts=1, cold=False, **kw):
        return {"status": status, "latency_s": lat, "attempts": attempts, "outcome": "ok", "code": None, "reason": None, "input_chars": 100, "phase_wall_s": 1.0, "kind": "extract", "cold": cold, **kw}
    recs = [rec(5.0, cold=True), rec(1.0), rec(2.0), rec(4.0, attempts=2), rec(3.0, status=503, attempts=2, code="UNAVAILABLE", reason="OUTPUT_INVALID")]
    s = measure.summarize(recs, "extract")
    assert s["cold_first_call"]["latency_s"] == 5.0
    assert s["after_cold_ok"]["n"] == 3 and s["after_cold_ok"]["max"] == 4.0
    assert s["after_cold_first_attempt"]["n"] == 2 and s["retried"]["n"] == 2 and s["failed"]["n"] == 1
    assert s["errors"] == {"503/UNAVAILABLE/OUTPUT_INVALID": 1} and s["success_rate"] == 0.8


def test_judge_a_compares_with_gold():
    gold = json.dumps({"action": "NEXT", "next_item": "Q2", "values": [{"key": "Q1.a"}, {"key": "Q1.b"}]})
    same_keys = {"status": 200, "text": json.dumps({"action": "NEXT", "next_item": "Q3", "values": [{"key": "Q1.b"}, {"key": "Q1.a"}]})}
    measure.judge_a(same_keys, gold)
    assert (same_keys["m_action"], same_keys["m_next_item"], same_keys["m_value_keys"], same_keys["m_exact"]) == (True, False, True, False)


def test_run_session_with_fake_backend_end_to_end(tmp_path):
    """preflight -> 번들 -> 릴리스 서버 -> 추론 서버(fake) -> 적재 -> 측정 -> 정리까지 한 바퀴. GPU와 vLLM 없이."""
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    smi_cmd = smi(tmp_path, 300)
    root = tmp_path / "write_root"
    env = {**os.environ, "GPU_SESSION_WRITE_ROOT": str(root), "NVIDIA_SMI": smi_cmd}
    r = subprocess.run(["bash", str(KIT / "run_session.sh"), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT), "--adapter-a", str(tmp_path / "aa"),
                        "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1), "--n-a", "3", "--n-b", "3", "--concurrency", "1,2", "--budget-min", "5"],
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    run = next((root / "mr-session").glob("run-*"))
    res = json.loads((run / "measure.json").read_text(encoding="utf-8"))
    assert res["put"]["state"] == "LOADED"
    assert set(res["summary"]) == {"extract@1", "respond@1", "extract@2", "respond@2"}
    assert res["summary"]["extract@1"]["ok"] == 3 and res["summary"]["extract@1"]["cold_first_call"] is not None
    assert "extract@2" in res["summary"] and "cold_first_call" in res["summary"]["extract@2"]
    assert not (run / "bundles").exists() and not (run / "release").exists()   # 정리됨
    assert (run / "logs" / "gpu_mem.log").exists()
    assert "프롬프트" not in (run / "measure.json").read_text(encoding="utf-8")   # 발화·프롬프트 원문은 결과에 남기지 않는다


def test_run_session_refuses_busy_gpu_and_starts_nothing(tmp_path):
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    root = tmp_path / "write_root"
    r = subprocess.run(["bash", str(KIT / "run_session.sh"), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT), "--adapter-a", str(tmp_path / "aa"),
                        "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1)], capture_output=True, text=True,
                       env={**os.environ, "GPU_SESSION_WRITE_ROOT": str(root), "NVIDIA_SMI": smi(tmp_path, 91000, "7, other, 91000")}, timeout=60)
    assert r.returncode == 2 and "거부" in r.stdout
    run = next((root / "mr-session").glob("run-*"))
    assert not (run / "release").exists() and not (run / "logs" / "server.log").exists()


def test_run_session_refuses_work_dir_outside_write_root(tmp_path):
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    r = subprocess.run(["bash", str(KIT / "run_session.sh"), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT), "--adapter-a", str(tmp_path / "aa"),
                        "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1), "--work", str(tmp_path / "elsewhere")], capture_output=True, text=True,
                       env={**os.environ, "GPU_SESSION_WRITE_ROOT": str(tmp_path / "wr"), "NVIDIA_SMI": smi(tmp_path, 1)}, timeout=60)
    assert r.returncode != 0 and "쓰기 허용 폴더" in r.stderr
