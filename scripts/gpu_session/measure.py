#!/usr/bin/env python3
"""떠 있는 model-runtime 서버에 C1 val의 A·B 행을 보내 지연과 출력 형식 통과율을 잰다(GPU 세션 키트, 표준 라이브러리만).

보내는 것: lora_a_val.jsonl의 user 메시지(ActionExtractionInput JSON) -> POST /v1/releases/{id}/extract, lora_b_val.jsonl의 user 메시지(ResponseRequest JSON) -> POST .../respond.
호출마다 기록: 지연(초), HTTP 상태, 오류 코드와 details.reason(OUTPUT_INVALID, ENGINE_TIMEOUT 등), X-Openit-Attempts, X-Openit-Outcome.
표(엔드포인트 x 동시 요청 수): 호출 수, 성공률(HTTP 200), 콜드 첫 호출(따로), 이후 p50/p95/max(성공 호출), 재시도 호출(attempts>1)과 실패 호출(따로).
정답 비교(정답 = 행의 assistant 메시지): A는 action 일치율, next_item 일치율, values 키 집합 일치율, 정확히 같은 JSON 비율. B는 출력이 비어 있지 않은 비율과 대체 문장(fallback) 비율.
**B의 내용 정확도는 평가 범위 밖이다**(문장이 맞는지는 이 도구가 판단하지 않는다). A의 일치율도 val 한 분할에서의 값일 뿐 모델 품질 판정이 아니다.
발화·모델 출력 원문은 결과 파일에 남기지 않는다(scenario_id, 길이, 판정만).

사용: measure.py --url http://127.0.0.1:PORT --release-id ID --c1-a lora_a_val.jsonl --c1-b lora_b_val.jsonl [--n-a 200] [--n-b 200] [--concurrency 1,4] [--out result.json] [--max-seconds 900]
종료 코드: 0 = 측정 끝(OUTPUT_INVALID가 있어도 0: 측정 도구이지 합격 판정이 아니다), 2 = 사용 오류, 3 = 서버에 닿지 못함, 4 = 시간 상한으로 중단(부분 결과는 남긴다)."""
from __future__ import annotations

import argparse
import os
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_INPUT_CHARS = 2500   # docs/boundary.md의 서버 입력 상한(추정값). 보고용 비교에만 쓴다


def gpu_mem_now() -> str | None:
    """단계 사이 GPU 메모리(used, total MiB). nvidia-smi가 없으면 None. NVIDIA_SMI로 바꿀 수 있다(시험)."""
    import subprocess
    try:
        r = subprocess.run([os.environ.get("NVIDIA_SMI", "nvidia-smi"), "--query-gpu=memory.used,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        return r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def parse_plan(text: str) -> list[tuple[int, int]]:
    """'1:20,4:20' -> [(동시 1, 각 20건), (동시 4, 각 20건)]. 같은 동시 수를 두 번 쓰면 결과 키(kind@conc)가 겹치므로 거부한다."""
    out = []
    for part in text.split(","):
        c, n = part.split(":")
        out.append((int(c), int(n)))
    if not out or any(c < 1 or n < 1 for c, n in out) or len({c for c, _ in out}) != len(out):
        raise ValueError(text)
    return out


def load_rows(path: str, n: int, mode: str) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if n <= 0 or not rows:
        return []
    if n < len(rows) and mode == "stride":   # 처음 n개는 앞쪽 시나리오에 쏠리므로 일정 간격으로 고른다(결정적)
        step = len(rows) / n
        rows = [rows[int(i * step)] for i in range(n)]
    else:
        rows = rows[:n]
    out = []
    for r in rows:
        u = next(m["content"] for m in r["messages"] if m["role"] == "user")
        gold = next(m["content"] for m in r["messages"] if m["role"] == "assistant")
        out.append({"scenario_id": r.get("scenario_id"), "body": json.loads(u), "gold": gold})
    return out


def pct(sorted_vals: list[float], p: float) -> float | None:
    """nearest-rank 백분위(probe-serving과 같은 방식)."""
    if not sorted_vals:
        return None
    return sorted_vals[max(0, math.ceil(p * len(sorted_vals)) - 1)]


def call(url: str, body: dict, token: str | None, timeout: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    t = time.monotonic()
    rec = {"status": None, "reason": None, "code": None, "attempts": None, "outcome": None, "text": None, "json": None, "input_chars": len(json.dumps(body, ensure_ascii=False))}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(); rec["status"] = r.status; hdr = r.headers
            rec["text"] = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read(); rec["status"] = e.code; hdr = e.headers
        try:
            b = json.loads(raw)
            rec["code"] = b.get("code"); d = b.get("details") or {}
            rec["reason"] = d.get("reason"); rec["details_attempts"] = d.get("attempts")
        except (ValueError, AttributeError):
            rec["code"] = "NON_JSON_ERROR"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        rec["status"] = 0; rec["code"] = "CONNECTION_ERROR"; rec["reason"] = type(e).__name__; hdr = {}
    rec["latency_s"] = time.monotonic() - t
    a = hdr.get("X-Openit-Attempts") if hdr else None
    rec["attempts"] = int(a) if a and a.isdigit() else rec.get("details_attempts")
    rec["outcome"] = hdr.get("X-Openit-Outcome") if hdr else None
    return rec


def put_release(base: str, rid: str, token: str | None, limit_s: float) -> dict:
    """PUT으로 적재를 시작하고 LOADED/FAILED가 될 때까지 기다린다. 응답 본문의 오류 코드만 남긴다."""
    h = {"Authorization": "Bearer " + token} if token else {}
    t = time.monotonic(); http = None; state = "ERROR"; err = None
    def req(method):
        r = urllib.request.Request(f"{base}/v1/releases/{rid}", method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=limit_s) as x:
                return x.status, json.loads(x.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError) as e:
            return 0, {"code": type(e).__name__}
    http, body = req("PUT")
    state = body.get("state") or ("ERROR" if http != 200 else "?")
    err = body.get("code") or (body.get("error") or {}).get("code")
    while state == "LOADING" and time.monotonic() - t < limit_s:
        time.sleep(3)
        _, body = req("GET"); state = body.get("state", "ERROR"); err = (body.get("error") or {}).get("code")
    return {"state": state, "http": http, "error_code": err, "seconds": time.monotonic() - t}


def judge_a(rec: dict, gold: str) -> None:
    if rec["status"] != 200:
        return
    try:
        out = json.loads(rec["text"]); g = json.loads(gold)
    except ValueError:
        rec["parse_error"] = True; return
    rec["m_action"] = out.get("action") == g.get("action")
    rec["m_next_item"] = out.get("next_item") == g.get("next_item")
    keys = lambda x: sorted({v.get("key") for v in x.get("values", []) if isinstance(v, dict)})   # noqa: E731
    rec["m_value_keys"] = keys(out) == keys(g)
    rec["m_exact"] = out == g


def judge_b(rec: dict) -> None:
    if rec["status"] == 200:
        rec["nonempty"] = bool((rec["text"] or "").strip())
        rec["fallback"] = rec["outcome"] == "fallback"


def run_phase(base: str, rid: str, kind: str, rows: list[dict], conc: int, token, timeout: float, deadline: float | None, cold_done: set) -> list[dict]:
    path = {"extract": "extract", "respond": "respond"}[kind]
    url = f"{base}/v1/releases/{rid}/{path}"
    results: list[dict | None] = [None] * len(rows)
    t0 = time.monotonic()

    def one(i: int):
        if deadline is not None and time.monotonic() > deadline:
            return
        rec = call(url, rows[i]["body"], token, timeout)
        rec.update(kind=kind, idx=i, concurrency=conc, scenario_id=rows[i]["scenario_id"])
        (judge_a(rec, rows[i]["gold"]) if kind == "extract" else judge_b(rec))
        rec.pop("json", None)
        results[i] = rec
    # 콜드 첫 호출: 서버 시작 뒤 이 엔드포인트의 첫 호출은 혼자 먼저 보낸다(동시 요청에 섞이지 않게)
    start = 0
    if kind not in cold_done and rows:
        one(0); cold_done.add(kind)
        if results[0]:
            results[0]["cold"] = True
        start = 1
    if conc <= 1:
        for i in range(start, len(rows)):
            one(i)
    else:
        with ThreadPoolExecutor(conc) as ex:
            list(ex.map(one, range(start, len(rows))))
    wall = time.monotonic() - t0
    out = [r for r in results if r is not None]
    for r in out:
        r["phase_wall_s"] = wall
    return out


def summarize(recs: list[dict], kind: str) -> dict:
    n = len(recs)
    ok = [r for r in recs if r["status"] == 200]
    cold = next((r for r in recs if r.get("cold")), None)
    rest = [r for r in recs if not r.get("cold")]
    rest_ok = [r for r in rest if r["status"] == 200]
    lat = lambda rs: sorted(r["latency_s"] for r in rs)   # noqa: E731
    stat = lambda rs: {"n": len(rs), "p50": pct(lat(rs), .5), "p95": pct(lat(rs), .95), "max": (max(lat(rs)) if rs else None)}   # noqa: E731
    errs = {}
    for r in recs:
        if r["status"] != 200:
            k = f"{r['status']}/{r['code']}/{r['reason']}"
            errs[k] = errs.get(k, 0) + 1
    s = {"calls": n, "ok": len(ok), "success_rate": (len(ok) / n if n else None), "errors": errs,
         "cold_first_call": ({"latency_s": cold["latency_s"], "status": cold["status"], "attempts": cold["attempts"]} if cold else None),
         "after_cold_ok": stat(rest_ok),
         "after_cold_first_attempt": stat([r for r in rest_ok if (r["attempts"] or 1) == 1]),
         "retried": stat([r for r in rest if (r["attempts"] or 1) > 1]),
         "failed": stat([r for r in rest if r["status"] != 200]),
         "attempts_dist": {}, "outcome_dist": {}}
    for r in recs:
        s["attempts_dist"][str(r["attempts"])] = s["attempts_dist"].get(str(r["attempts"]), 0) + 1
        s["outcome_dist"][str(r["outcome"])] = s["outcome_dist"].get(str(r["outcome"]), 0) + 1
    if recs:
        s["wall_s"] = recs[0]["phase_wall_s"]; s["throughput_per_s"] = n / recs[0]["phase_wall_s"] if recs[0]["phase_wall_s"] else None
    chars = sorted(r["input_chars"] for r in recs)
    s["input_chars"] = {"p50": pct(chars, .5), "p95": pct(chars, .95), "max": chars[-1] if chars else None, "over_limit": sum(c > MAX_INPUT_CHARS for c in chars)}
    # 입력 길이 구간별 p50(길이가 지연에 미치는 영향 보기: 입력 상한 재정용). 구간은 입력 글자 수의 3분위
    if len(rest_ok) >= 9:
        cs = sorted(r["input_chars"] for r in rest_ok); a, b = cs[len(cs) // 3], cs[2 * len(cs) // 3]
        bands = {"<=%d" % a: [r for r in rest_ok if r["input_chars"] <= a], "<=%d" % b: [r for r in rest_ok if a < r["input_chars"] <= b], ">%d" % b: [r for r in rest_ok if r["input_chars"] > b]}
        s["latency_by_input_chars"] = {k: {"n": len(v), "p50": pct(lat(v), .5), "p95": pct(lat(v), .95)} for k, v in bands.items()}
    if kind == "extract":
        rate = lambda k: (sum(1 for r in ok if r.get(k)) / len(ok) if ok else None)   # noqa: E731
        s["vs_gold"] = {"action_match": rate("m_action"), "next_item_match": rate("m_next_item"), "value_keys_match": rate("m_value_keys"), "exact_json_match": rate("m_exact"),
                        "denominator": f"HTTP 200 응답 {len(ok)}건(실패한 호출은 제외)", "unparsable_200": sum(1 for r in ok if r.get("parse_error"))}
    else:
        s["vs_gold"] = {"nonempty_rate": (sum(1 for r in ok if r.get("nonempty")) / len(ok) if ok else None),
                        "fallback_rate": (sum(1 for r in ok if r.get("fallback")) / len(ok) if ok else None),
                        "note": "내용 정확도는 평가 범위 밖. nonempty는 형식 지표일 뿐이다"}
    return s


def f(v, nd=3):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def render(report: dict) -> str:
    L = [f"== 측정 결과 (서버 {report['url']}, 릴리스 {report['release_id']})", ""]
    for key, s in report["summary"].items():
        kind, conc = key.split("@")
        L.append(f"-- {kind} 동시 {conc}: 호출 {s['calls']}, 성공 {s['ok']} ({f(s['success_rate'] and s['success_rate'] * 100, 1)}%), 걸린 시간 {f(s.get('wall_s'), 1)}s, 처리량 {f(s.get('throughput_per_s'), 2)}/s")
        L.append(f"   {'구분':<22}{'n':>5}{'p50':>9}{'p95':>9}{'max':>9}  (초)")
        c = s["cold_first_call"]
        if c:
            L.append(f"   {'콜드 첫 호출(상태 %s)' % c['status']:<22}{1:>5}{f(c['latency_s']):>9}{'-':>9}{f(c['latency_s']):>9}")
        for label, k in (("이후 성공 호출", "after_cold_ok"), ("  attempts=1", "after_cold_first_attempt"), ("재시도(attempts>1)", "retried"), ("실패 호출", "failed")):
            x = s[k]
            L.append(f"   {label:<22}{x['n']:>5}{f(x['p50']):>9}{f(x['p95']):>9}{f(x['max']):>9}")
        if s["errors"]:
            L.append("   오류: " + ", ".join(f"{k} x{v}" for k, v in sorted(s["errors"].items())))
        L.append(f"   attempts 분포 {s['attempts_dist']}, outcome 분포 {s['outcome_dist']}")
        ic = s["input_chars"]
        L.append(f"   입력 글자 수 p50 {f(ic['p50'])} p95 {f(ic['p95'])} max {f(ic['max'])}, 상한({MAX_INPUT_CHARS}) 초과 {ic['over_limit']}건")
        if "latency_by_input_chars" in s:
            L.append("   입력 길이 구간별 p50/p95: " + ", ".join(f"{k}자 n={v['n']} {f(v['p50'])}/{f(v['p95'])}" for k, v in s["latency_by_input_chars"].items()))
        g = s["vs_gold"]
        if kind == "extract":
            L.append(f"   정답 비교({g['denominator']}): action {f(g['action_match'])}, next_item {f(g['next_item_match'])}, values 키 집합 {f(g['value_keys_match'])}, 정확히 같은 JSON {f(g['exact_json_match'])}")
        else:
            L.append(f"   B 출력이 비지 않은 비율 {f(g['nonempty_rate'])}, 대체 문장(fallback) 비율 {f(g['fallback_rate'])}  ({g['note']})")
        L.append("")
    if report.get("truncated"):
        L.append("!! 시간 상한(--max-seconds)에 걸려 일부만 측정했다.")
    L.append("해석: 공유 GPU라 다른 작업과 경합하면 지연이 부풀 수 있다(docs/GPU_SESSION.md). 이 숫자는 합격 판정이 아니라 재정을 위한 입력이다.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--release-id", required=True)
    ap.add_argument("--c1-a", required=True)
    ap.add_argument("--c1-b", required=True)
    ap.add_argument("--n-a", type=int, default=200)
    ap.add_argument("--n-b", type=int, default=200)
    ap.add_argument("--concurrency", default="1", help="쉼표로 구분한 동시 요청 수 목록(예: 1,4). 첫 값에서 콜드 첫 호출을 잰다")
    ap.add_argument("--plan", help="한 번의 서버 기동으로 이어 돌릴 부하 단계 '동시:건수,...'(예: 1:20,4:20 = 동시 1에서 A·B 각 20건, 이어 동시 4에서 각 20건). 주면 --concurrency/--n-a/--n-b를 대신한다. 단계 사이 GPU 메모리를 결과(stage_gpu)에 남긴다")
    ap.add_argument("--sample", choices=("stride", "first"), default="stride")
    ap.add_argument("--token-file", help="Bearer 토큰 파일(값은 출력하지 않는다). 개발 서버(ALLOW_NO_AUTH)면 생략")
    ap.add_argument("--timeout", type=float, default=120.0, help="호출 하나의 소켓 timeout(초). 서버의 요청 상한보다 충분히 길게")
    ap.add_argument("--max-seconds", type=float, default=0, help="전체 시간 상한(초). 넘으면 남은 호출을 건너뛰고 종료 코드 4. 0이면 상한 없음")
    ap.add_argument("--put-release", action="store_true", help="측정 전에 PUT /v1/releases/{id}로 적재하고 걸린 시간을 기록한다(202면 LOADED까지 기다린다)")
    ap.add_argument("--put-timeout", type=float, default=900.0, help="적재 대기 상한(초)")
    ap.add_argument("--out", help="결과 JSON 경로(표는 같은 이름의 .txt)")
    a = ap.parse_args()
    try:
        if a.plan:
            plan = parse_plan(a.plan)
        else:
            plan = [(int(x), None) for x in a.concurrency.split(",")]
            assert all(c >= 1 for c, _ in plan)
    except (ValueError, AssertionError):
        print("--plan은 '동시:건수'의 쉼표 목록(동시 수 중복 없음), --concurrency는 1 이상 정수의 쉼표 목록이어야 한다", file=sys.stderr); return 2
    concs = [c for c, _ in plan]
    token = Path(a.token_file).read_text().strip() if a.token_file else None
    base = a.url.rstrip("/")
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=10):
            pass
    except (urllib.error.URLError, OSError) as e:
        print(f"서버에 닿지 못했다({type(e).__name__}): {base}", file=sys.stderr); return 3
    put = None
    if a.put_release:
        put = put_release(base, a.release_id, token, a.put_timeout)
        print(f"적재: {put['state']} ({put['seconds']:.1f}s, HTTP {put['http']})", flush=True)
        if put["state"] != "LOADED":
            if a.out:
                Path(a.out).write_text(json.dumps({"url": base, "release_id": a.release_id, "put": put}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"적재 실패: {put}", file=sys.stderr); return 3
    na = max(n for _, n in plan) if a.plan else a.n_a
    nb = na if a.plan else a.n_b
    rows = {"extract": load_rows(a.c1_a, na, a.sample), "respond": load_rows(a.c1_b, nb, a.sample)}
    deadline = time.monotonic() + a.max_seconds if a.max_seconds > 0 else None
    report = {"url": base, "release_id": a.release_id, "n": {k: len(v) for k, v in rows.items()}, "concurrency": concs, "sample": a.sample,
              "started_at_unix": time.time(), "put": put, "plan": a.plan, "stage_gpu": {"after_load": gpu_mem_now()}, "summary": {}, "calls": [], "truncated": False}
    try:
        report["info"] = json.loads(urllib.request.urlopen(base + "/v1/info", timeout=10).read())
    except Exception:   # noqa: BLE001  info는 있으면 좋은 정보
        report["info"] = None
    cold_done: set = set()
    for conc, n_stage in plan:
        for kind in ("extract", "respond"):
            stage_rows = rows[kind][:n_stage] if n_stage else rows[kind]
            recs = run_phase(base, a.release_id, kind, stage_rows, conc, token, a.timeout, deadline, cold_done)
            if len(recs) < len(stage_rows):
                report["truncated"] = True
            if recs:
                report["summary"][f"{kind}@{conc}"] = summarize(recs, kind)
                report["calls"] += [{k: v for k, v in r.items() if k != "text"} for r in recs]
        report["stage_gpu"][f"after_concurrency_{conc}"] = gpu_mem_now()   # 단계 사이 GPU 메모리(재기동 없이 이어 돌 때 누수·증가를 보려고)
    if all(r["status"] == 0 for r in report["calls"]) and report["calls"]:
        print("모든 호출이 연결 오류다. 서버를 확인한다.", file=sys.stderr)
    text = render(report)
    print(text)
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        Path(a.out).with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    # 성공 호출이 하나도 없으면 실패다. 종료 코드 0이면 임대 시간을 쓰고도 빈 결과를 성공으로 오해한다(gpu-dev 첫 실행에서 어댑터 파일이 깨져 전부 503인데 0으로 끝났다)
    if not any(r["status"] == 200 for r in report["calls"]):
        print("성공한 호출이 하나도 없다(종료 코드 7). 서버 로그(logs/server.log)를 본다.", file=sys.stderr)
        return 7
    return 4 if report["truncated"] else 0


if __name__ == "__main__":
    sys.exit(main())
