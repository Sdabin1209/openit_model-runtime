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
    hdr = {"base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": {"dtype": "F32", "shape": [r, 8], "data_offsets": [0, r * 8 * 4]},
           "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight": {"dtype": "F32", "shape": [8, r], "data_offsets": [r * 8 * 4, r * 8 * 8]}}
    hb = json.dumps(hdr).encode()   # 실제 safetensors와 같은 구조: 8바이트 길이 + JSON 헤더 + 데이터
    (d / "adapter_model.safetensors").write_bytes(len(hb).to_bytes(8, "little") + hb + b"\0" * (r * 8 * 8))
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


@pytest.mark.parametrize("via_base", [False, True])
def test_run_session_with_fake_backend_end_to_end(tmp_path, via_base):   # via_base: 환경 변수 대신 --base로 쓰기 뿌리를 주는 경로(RunPod /workspace/dabin)
    """preflight -> 번들 -> 릴리스 서버 -> 추론 서버(fake) -> 적재 -> 측정 -> 정리까지 한 바퀴. GPU와 vLLM 없이."""
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    smi_cmd = smi(tmp_path, 300)
    root = tmp_path / "write_root"
    env = {**os.environ, "NVIDIA_SMI": smi_cmd}
    if not via_base:
        env["GPU_SESSION_WRITE_ROOT"] = str(root)
    r = subprocess.run(["bash", str(KIT / "run_session.sh"), *(["--base", str(root)] if via_base else []), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT), "--adapter-a", str(tmp_path / "aa"),
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


def _snapshot(base):
    return sorted(str(p.relative_to(base)) for p in Path(base).rglob("*"))


def _inputs_c1(tmp_path):
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    return c1


def _session(tmp_path, c1, root, *extra, work=None, budget="5"):
    cmd = ["bash", str(KIT / "run_session.sh"), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT), "--adapter-a", str(tmp_path / "aa"),
           "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1), "--n-a", "2", "--n-b", "2", "--concurrency", "1", "--budget-min", budget, *extra]
    if work:
        cmd += ["--work", str(work)]
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GPU_SESSION_WRITE_ROOT": str(root), "NVIDIA_SMI": smi(tmp_path, 300)}, timeout=180)


def test_run_session_outside_root_creates_nothing(tmp_path):   # MR-1: 거부돼도 쓰기 허용 폴더 밖에 디렉터리가 생기지 않는다
    c1 = _inputs_c1(tmp_path)
    root = tmp_path / "root"; root.mkdir()
    smi(tmp_path, 300)   # 가짜 nvidia-smi 파일을 먼저 만든다
    before = _snapshot(tmp_path)
    r = _session(tmp_path, c1, root, work=tmp_path / "outside" / "deep")
    assert r.returncode != 0 and "쓰기 허용 폴더" in r.stderr
    assert _snapshot(tmp_path) == before, "거부했는데 파일시스템이 바뀌었다"
    assert not (tmp_path / "outside").exists()
    link = tmp_path / "root" / "lnk"; link.symlink_to(tmp_path / "outside2")   # 허용 폴더 안의 링크가 밖을 가리켜도 거부
    r = _session(tmp_path, c1, root, work=link / "w")
    assert r.returncode != 0 and not (tmp_path / "outside2").exists()


def test_clean_cache_only_removes_a_cache_this_kit_made(tmp_path):   # MR-2
    c1 = _inputs_c1(tmp_path)
    root = tmp_path / "root"
    foreign = root / "mine" / "cache"; foreign.mkdir(parents=True)
    (foreign / "keep.txt").write_text("남의 파일")
    r = _session(tmp_path, c1, root, "--clean-cache", work=root / "mine")
    assert r.returncode != 0 and "만든 캐시가 아니다" in r.stderr
    assert (foreign / "keep.txt").read_text() == "남의 파일" and not list((root / "mine").glob("run-*"))   # 아무것도 지우거나 만들지 않았다
    r = _session(tmp_path, c1, root, "--clean-cache", work=root / "fresh")                              # 키트가 만든 캐시는 지운다
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
    assert not (root / "fresh" / "cache").exists()
    r = _session(tmp_path, c1, root, work=root / "fresh")                                               # 옵션이 없으면 남기고 표식이 있다
    assert r.returncode == 0 and (root / "fresh" / "cache" / ".mr-session-owned").exists()
    (root / "fresh" / "cache" / ".mr-session-owned").unlink()                                           # 표식이 사라지면 --clean-cache가 지우지 않는다
    (root / "fresh" / "cache" / "x").write_text("x")
    r = _session(tmp_path, c1, root, "--clean-cache", work=root / "fresh")
    assert r.returncode != 0 and (root / "fresh" / "cache" / "x").exists()


def test_prepare_bundle_rejects_dangerous_release_ids_and_foreign_overwrite(tmp_path):   # prepare_bundle 검증
    setup_inputs(tmp_path)
    sibling = tmp_path / "out" / "sibling.txt"; sibling.parent.mkdir(); sibling.write_text("형제")
    for bad in ("..", ".", "../x", "a/b", "/", "a b", ""):
        r = run_prepare(tmp_path, "--release-id", bad, "--overwrite")
        assert r.returncode != 0, bad
    assert sibling.read_text() == "형제"
    assert run_prepare(tmp_path, "--release-id", "rel-1").returncode == 0
    assert (tmp_path / "out" / "rel-1" / ".mr-session-owned").exists()
    assert run_prepare(tmp_path, "--release-id", "rel-1").returncode != 0                      # --overwrite 없이는 거부
    assert run_prepare(tmp_path, "--release-id", "rel-1", "--overwrite").returncode == 0       # 이 도구가 만든 것은 덮어쓴다
    foreign = tmp_path / "out" / "foreign"; foreign.mkdir(); (foreign / "f.txt").write_text("남의 것")
    r = run_prepare(tmp_path, "--release-id", "foreign", "--overwrite")
    assert r.returncode != 0 and (foreign / "f.txt").exists()                                  # 표식 없는 폴더는 지우지 않는다


def test_options_without_value_exit_2_instead_of_hanging(tmp_path):   # 인자 파싱
    for script, opts in (("preflight.sh", ("--until", "--max-used-mib", "--min-free-mib")),
                         ("run_session.sh", ("--until", "--work", "--adapter-a", "--c1-dir", "--backend"))):
        for o in opts:
            r = subprocess.run(["bash", str(KIT / script), o], capture_output=True, text=True, timeout=10, env={**os.environ, "NVIDIA_SMI": smi(tmp_path, 1)})
            assert r.returncode == 2 and "값이 필요" in r.stderr, (script, o, r.returncode)


def test_token_is_not_in_curl_arguments():   # 토큰은 헤더 파일로만 전달한다
    text = (KIT / "run_session.sh").read_text(encoding="utf-8")
    assert 'Bearer $(cat' not in text and '-H "@$RUN/auth_header"' in text


def test_hf_home_is_pinned_before_xdg_redirect():   # gpu-dev 실행에서 발견: XDG_CACHE_HOME을 옮기면 이미 받은 모델(HF 캐시)이 안 보여 LocalEntryNotFoundError
    sh = (KIT / "run_session.sh").read_text(encoding="utf-8")
    assert sh.index("export HF_HOME=") < sh.index('XDG_CACHE_HOME="$C/xdg"')


def test_measure_exit_code_7_when_no_call_succeeds():   # 전부 실패인데 0으로 끝나면 임대 시간만 쓰고 성공으로 오해한다
    src = (KIT / "measure.py").read_text(encoding="utf-8")
    assert "return 7" in src and 'r["status"] == 200' in src


def test_kit_contains_gpu_lock_and_pod_scripts_parse():
    for n in ("pod_setup.sh", "pod_run.sh"):
        assert subprocess.run(["bash", "-n", str(KIT / n)]).returncode == 0
    assert "requirements-gpu.lock" in (KIT / "make_kit.sh").read_text(encoding="utf-8")


def test_pod_run_refuses_without_upload_confirmation(tmp_path):   # 데이터 외부 반출은 사용자 결정: 확인 플래그 없이는 아무것도 보내지 않는다
    ad = tmp_path / "a"; ad.mkdir(); (ad / "adapter_config.json").write_text("{}"); (ad / "adapter_model.safetensors").write_bytes(b"x")
    c1 = tmp_path / "c"; c1.mkdir(); (c1 / "lora_a_val.jsonl").write_text("{}\n"); (c1 / "lora_b_val.jsonl").write_text("{}\n")
    r = subprocess.run(["bash", str(KIT / "pod_run.sh"), "--ssh", "-p 1 nobody@127.0.0.1", "--adapter-a", str(ad), "--adapter-b", str(ad), "--c1-dir", str(c1)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 3 and "아무것도 보내지 않았다" in r.stdout


def test_prepare_bundle_rejects_bad_safetensors_header(tmp_path):   # 헤더만 읽는 정적 검사: 잘림, rank 불일치, LoRA 아닌 텐서
    setup_inputs(tmp_path)
    p = tmp_path / "aa" / "adapter_model.safetensors"
    good = p.read_bytes()
    p.write_bytes(good[:-5])
    r = run_prepare(tmp_path)
    assert r.returncode != 0 and "잘린" in (r.stderr + r.stdout)
    p.write_bytes(good)
    (tmp_path / "aa" / "adapter_config.json").write_text(json.dumps({"r": 8, "base_model_name_or_path": "Qwen/Qwen3-8B", "target_modules": ["q_proj"]}))
    r = run_prepare(tmp_path)
    assert r.returncode != 0 and "rank" in (r.stderr + r.stdout)
    (tmp_path / "aa" / "adapter_config.json").write_text(json.dumps({"r": 16, "base_model_name_or_path": "Qwen/Qwen3-8B", "target_modules": ["q_proj"], "use_dora": True}))
    r = run_prepare(tmp_path)
    assert r.returncode != 0 and "use_dora" in (r.stderr + r.stdout)


# ---- pod_setup.sh 단계 분리·인자 처리 (ADR 0014). 네트워크·GPU 없이 확인 가능한 부분만 ----
def pod_setup(*args, env=None):
    return subprocess.run(["bash", str(KIT / "pod_setup.sh"), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})


def test_pod_setup_syntax_ok():
    assert subprocess.run(["bash", "-n", str(KIT / "pod_setup.sh")]).returncode == 0


def test_pod_setup_rejects_unknown_argument(tmp_path):
    r = pod_setup("--base", str(tmp_path), "--nope")
    assert r.returncode == 64 and "알 수 없는 인자" in r.stderr


def test_pod_setup_rejects_unknown_stage_before_touching_base(tmp_path):
    base = tmp_path / "b"
    r = pod_setup("--base", str(base), "--stage", "bogus")
    assert r.returncode == 64 and "--stage" in r.stderr
    assert not base.exists()  # 인자 오류는 아무것도 만들기 전에 거부


def test_pod_setup_gpu_stage_stops_without_nvidia_smi(tmp_path):
    r = pod_setup("--base", str(tmp_path), "--stage", "gpu", env={"PATH": "/usr/bin:/bin"} if not Path("/usr/bin/nvidia-smi").exists() and not Path("/bin/nvidia-smi").exists() else None)
    if r.returncode == 0:
        pytest.skip("이 호스트에는 nvidia-smi가 있다")
    assert r.returncode == 1 and "nvidia-smi" in r.stderr


def test_pod_setup_install_stage_needs_prereq_first(tmp_path):
    r = pod_setup("--base", str(tmp_path), "--stage", "install")
    assert r.returncode == 1 and "--stage prereq" in r.stderr


def test_pod_setup_verify_and_model_need_venv(tmp_path):
    for st in ("verify", "model"):
        r = pod_setup("--base", str(tmp_path), "--stage", st)
        assert r.returncode == 1 and "venv가 없다" in r.stderr


def test_pod_setup_prereq_stops_on_low_disk_before_installing(tmp_path):
    r = pod_setup("--base", str(tmp_path), "--stage", "prereq", "--min-disk-gb", "99999999")
    assert r.returncode == 1 and "남은 디스크" in r.stderr
    assert not (tmp_path / "uv" / "uv").exists()


def test_pod_setup_pins_uv_version_and_hashes():
    s = (KIT / "pod_setup.sh").read_text()
    import re
    assert re.search(r'^UV_VERSION="\d+\.\d+\.\d+"', s, re.M)
    assert len(re.findall(r'^UV_SHA256_(?:x86_64|aarch64)="[0-9a-f]{64}"', s, re.M)) == 2
    assert "sudo " not in s.replace("sudo는", "").replace("sudo 없이", "")  # sudo 사용 금지


# ---- 모델 이름의 출처는 프로파일 하나 (ADR 0015) ----
def test_pod_setup_rejects_model_different_from_profile(tmp_path):
    r = pod_setup("--base", str(tmp_path / "b"), "--stage", "model", "--model", "Qwen/Qwen3-1.7B")
    assert r.returncode == 64 and "base_model(Qwen/Qwen3-8B)" in r.stderr and "LocalEntryNotFoundError" in r.stderr
    assert not (tmp_path / "b").exists()   # 아무것도 만들기 전에 중단


def test_pod_setup_reads_model_from_profile_and_accepts_same_model(tmp_path):
    prof = tmp_path / "p.json"; prof.write_text('{\n  "base_model": "Org/Tiny-1B",\n  "x": 1\n}\n')
    # model 단계는 venv가 없으면 멈추지만, 그 전의 프로파일 검사는 통과해야 한다(같은 --model은 허용)
    for extra in ([], ["--model", "Org/Tiny-1B"]):
        r = pod_setup("--base", str(tmp_path / "b"), "--stage", "model", "--profile", str(prof), *extra)
        assert r.returncode == 1 and "venv가 없다" in r.stderr
    r = pod_setup("--base", str(tmp_path / "b"), "--stage", "model", "--profile", str(prof), "--model", "Qwen/Qwen3-8B")
    assert r.returncode == 64 and "Org/Tiny-1B" in r.stderr


def test_pod_setup_stops_without_profile_or_base_model(tmp_path):
    r = pod_setup("--base", str(tmp_path / "b"), "--profile", str(tmp_path / "none.json"))
    assert r.returncode == 64 and "프로파일이 없다" in r.stderr
    bad = tmp_path / "bad.json"; bad.write_text('{"x": 1}')
    r = pod_setup("--base", str(tmp_path / "b"), "--profile", str(bad))
    assert r.returncode == 64 and "base_model이 없다" in r.stderr


def test_pod_setup_default_profile_is_the_one_pod_run_uses():
    run = (KIT / "pod_run.sh").read_text()
    assert 'PROFILE="rtx4090-24g.json"' in run and '--profile $BASE/mr-kit/profiles/$PROFILE' in run.split("pod_setup.sh")[1].split("\n")[0]
    assert 'PROFILE="rtx4090-24g.json"' in (KIT / "pod_setup.sh").read_text()


# ---- util 자동 재시도와 --plan, 키트 커밋 기록 (ADR 0016) ----
import util_retry  # noqa: E402


def test_util_retry_classifies_only_memory_shortage_as_retryable():
    mem1 = "ValueError: Free memory on device (5/11 GiB) on startup is less than desired GPU memory utilization (0.9, 10 GiB)."
    mem2 = "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"
    kv = "ValueError: No available memory for the cache blocks. Try increasing `gpu_memory_utilization` when initializing the engine."
    kv2 = "To serve at least one request with the max seq len (4096), (1.2 GiB KV cache is needed, which is larger than the available KV cache memory (0.5 GiB)"
    assert util_retry.classify(mem1)[0] == "memory" and util_retry.classify(mem2)[0] == "memory"
    assert util_retry.classify(kv)[0] == "other" and util_retry.classify(kv2)[0] == "other"   # util을 낮추면 더 나빠지는 종류
    assert util_retry.classify("ImportError: libcuda.so")[0] == "other"
    assert util_retry.next_util(0.9) == 0.85 and util_retry.next_util(0.85) == 0.8


def test_measure_parse_plan():
    assert measure.parse_plan("1:20,4:20") == [(1, 20), (4, 20)]
    for bad in ("1:20,1:5", "0:5", "x", "1:0", ""):
        with pytest.raises(ValueError):
            measure.parse_plan(bad)


def run_stub_session(tmp_path, fail, *extra):
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    root = tmp_path / "root"
    env = {**os.environ, "NVIDIA_SMI": smi(tmp_path, 300), "PYTHONPATH": str(ROOT / "tests"), "STUB_FAIL": fail}
    r = subprocess.run(["bash", str(KIT / "run_session.sh"), "--base", str(root), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT),
                        "--adapter-a", str(tmp_path / "aa"), "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1), "--server-module", "stub_oom_server",
                        "--util", "0.9", "--ready-timeout-min", "1", "--budget-min", "5", *extra], capture_output=True, text=True, env=env, timeout=170)
    return r, next((root / "mr-session").glob("run-*"))


def test_run_session_retries_lower_util_on_memory_shortage(tmp_path):
    r, run = run_stub_session(tmp_path, "memory", "--n-a", "1", "--n-b", "1")
    att = json.loads((run / "util_attempts.json").read_text(encoding="utf-8"))
    assert [(a["util"], a["outcome"]) for a in att] == [(0.9, "backend_failed_memory"), (0.85, "backend_failed_memory"), (0.8, "ready")]
    assert "less than desired GPU memory utilization" in att[0]["reason"]
    assert (run / "logs" / "server.attempt1.log").exists() and (run / "logs" / "server.attempt2.log").exists()
    res = json.loads((run / "measure.json").read_text(encoding="utf-8"))   # 가짜 서버라 적재는 실패(종료 3)하지만 util 기록은 남는다
    assert r.returncode == 3 and res["util_start"] == 0.9 and res["util_final"] == 0.8 and len(res["util_attempts"]) == 3


def test_run_session_stops_at_once_on_non_memory_failure(tmp_path):
    r, run = run_stub_session(tmp_path, "kv")
    att = json.loads((run / "util_attempts.json").read_text(encoding="utf-8"))
    assert r.returncode == 5 and [(a["util"], a["outcome"]) for a in att] == [(0.9, "backend_failed_other")]
    assert "재시도하지 않고" in r.stderr and not (run / "logs" / "server.attempt1.log").exists()


def test_run_session_plan_runs_stages_on_one_server_and_records_gpu(tmp_path):
    setup_inputs(tmp_path)
    c1 = tmp_path / "c1"; c1.mkdir()
    (tmp_path / "a.jsonl").rename(c1 / "lora_a_val.jsonl"); (tmp_path / "b.jsonl").rename(c1 / "lora_b_val.jsonl")
    root = tmp_path / "root"
    cmd = ["bash", str(KIT / "run_session.sh"), "--base", str(root), "--backend", "fake", "--python", sys.executable, "--kit", str(ROOT),
           "--adapter-a", str(tmp_path / "aa"), "--adapter-b", str(tmp_path / "ab"), "--c1-dir", str(c1), "--budget-min", "5"]
    env = {**os.environ, "NVIDIA_SMI": smi(tmp_path, 300)}
    r = subprocess.run([*cmd, "--plan", "1:2,2:3"], capture_output=True, text=True, env=env, timeout=170)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    run = next((root / "mr-session").glob("run-*"))
    res = json.loads((run / "measure.json").read_text(encoding="utf-8"))
    assert res["summary"]["extract@1"]["calls"] == 2 and res["summary"]["respond@2"]["calls"] == 3
    assert set(res["stage_gpu"]) == {"after_load", "after_concurrency_1", "after_concurrency_2"}
    assert (run / "logs" / "server.log").exists() and not list((run / "logs").glob("server.attempt*"))   # 서버는 한 번만 떴다
    both = subprocess.run([*cmd, "--plan", "1:2", "--n-a", "3"], capture_output=True, text=True, env=env, timeout=60)
    assert both.returncode == 64 and "같이 쓰지 않는다" in both.stderr


def test_make_kit_records_commit_and_logs_carry_it():
    mk = (KIT / "make_kit.sh").read_text(encoding="utf-8")
    assert "rev-parse HEAD" in mk and "-dirty" in mk
    assert "KIT_MANIFEST" in (KIT / "run_session.sh").read_text(encoding="utf-8") and "kit_commit" in (KIT / "run_session.sh").read_text(encoding="utf-8")
    assert "KIT_MANIFEST" in (KIT / "pod_setup.sh").read_text(encoding="utf-8") and "KIT_MANIFEST" in (KIT / "pod_run.sh").read_text(encoding="utf-8")
