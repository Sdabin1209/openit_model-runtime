"""release_registry 컴포넌트: 릴리스 적재 상태와 추론 호출의 직렬화. PutRequest/InferRequest -> HttpResult (HTTP.md "I9 ModelRuntime 추론 서버", D62).

이 컴포넌트는 HTTP를 모른다: 작업 하나 = 입력 -> (상태 코드, 본문, 헤더)인 함수 모양으로만 드러나고(DESIGN.md 2절), 받는 쪽 통신은 link_receive가 맡는다.
이웃·바깥 자원(번들 읽기 `reader_factory`, 세션 적재 `load_session`, 이웃 서버 도달성 `probe`, 시작 때 디렉터리 점검 `dirs_check`)은 wiring이 주입한다.

적재 실패는 이미 적재된 릴리스를 건드리지 않는다. 결과는 헤더 X-Openit-Outcome(extract는 ok, respond는 ok|fallback)|Attempts|Protections.
extract가 재시도 후에도 ActionExtraction을 지키지 못하면 값을 지어내지 않고 503 UNAVAILABLE(details.reason=OUTPUT_INVALID, D75)을 낸다.
시스템 프롬프트는 적재한 릴리스 패키지(어댑터 디렉터리의 파일)의 것만 쓴다(D75).
엔진 호출은 직렬화하고, 대기가 길 것 같으면 큐에 쌓지 않고 503으로 거절한다. 발화·출력 원문은 로그에 남기지 않는다.
스키마 밖 조건(schemas/README.md의 표): 대기열 길이·마감 같은 시간 의존 거절, 릴리스 상태(LOADED 여부), 입력 직렬화 길이 상한."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

from boundary_contracts.errors import BoundaryError

from ..wiring.errors import (BackendNotAvailable, ContractVersionMismatch, ContractViolation, ModelOutputInvalid, ServingConfigMismatch,
                             SystemPromptMismatch, UnsupportedServingConfig, violation_names)
from ..wiring.schema_gate import violations

INPUT, OUTPUT = "InferRequest", "HttpResult"
log = logging.getLogger("model_runtime.server")
MAX_INPUT_CHARS = 2500          # 입력(JSON 직렬화)의 문자 수 상한. 왜: max_model_len 4096 토큰 - 출력 512 - 시스템 프롬프트 약 1000(가정)을 1자=1토큰으로 보수 환산한 추정, 토큰 수 측정 안 함 (ADR 0012)
CONN_TIMEOUT_S = 15.0           # 왜: 느린 클라이언트·유휴 keep-alive가 스레드를 쥐지 못하게. 값 15는 측정·표준 근거 없는 초기값 (ADR 0012)
MAX_LOADING = 2                 # 왜: 적재는 디스크·GPU 메모리를 쓰는 무거운 작업. 값 2는 초기값 (ADR 0012)
MAX_FAILED_KEPT = 16            # 왜: 실패 항목이 메모리에 무한히 쌓이지 않게(감사 MR-4). 값 16은 초기값 (ADR 0012)
DEFAULT_DEADLINES_S = {"extract": 3.0, "respond": 2.0}   # 왜: 계약 CONVENTIONS '시간 상한 사슬' 2.2.0 표. 한 턴이 extract→respond 두 번이라 합 5.0s + 서버 처리 여유 1.0s = 턴 마감 6s. 측정하지 않은 배분값 (ADR 0020)
SUPPORTED_RUNTIME_CONTRACT_VERSION = 1   # 문진 스키마 정수 버전(brief 3절). survey_version_check와 같은 값이어야 한다(tests/test_structure.py)
STATUS = {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409, "FAILED_PRECONDITION": 412,
          "UNSUPPORTED_VERSION": 422, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}


def to_boundary(e: Exception) -> BoundaryError:
    """적재 중 예외를 BoundaryError로. 원문 없이 종류만 담는다."""
    if isinstance(e, BoundaryError):
        return e
    if isinstance(e, (ServingConfigMismatch, UnsupportedServingConfig, ContractViolation)) and not isinstance(e, BackendNotAvailable):
        return BoundaryError("FAILED_PRECONDITION", f"{type(e).__name__}: {str(e)[:200]}")
    if isinstance(e, SystemPromptMismatch):
        return BoundaryError("INTEGRITY_ERROR", f"{e.role}의 시스템 프롬프트 파일이 없거나 해시가 다르다")
    if isinstance(e, ContractVersionMismatch):
        return BoundaryError("UNSUPPORTED_VERSION", "문진 스키마 버전을 지원하지 않는다")
    return BoundaryError("UNAVAILABLE", f"{type(e).__name__}")


class Release:
    def __init__(self):
        self.state, self.error, self.session, self.hashes, self.prompt_hashes = "LOADING", None, None, None, None
        self.done = threading.Event()


class Service:
    def __init__(self, *, schema_dir: Path, bundle_dir: Path, base_backend_factory: Callable[[], object],
                 reader_factory: Callable[[], object], backend_name: str, load_session: Callable[..., object],
                 probe: Callable[[str], str | None], dirs_check: Callable[[Mapping], Mapping], runtime_version: str, facts: Mapping, tokens: Mapping[str, str] | None = None,
                 queue_wait_s: float = 3.0, put_wait_s: float = 3.0, max_releases: int = 4, test_hooks: Mapping | None = None,
                 release_url: str | None = None, release_required: bool = False, probe_ttl_s: float = 5.0,
                 hf_home: str | None = None, runtime_dirs: list[str] | None = None,
                 deadlines: Mapping[str, float] | None = None, backend_retry_s: float = 30.0, max_input_chars: int = MAX_INPUT_CHARS,   # 호출별 상한 extract 3.0s·respond 2.0s: 계약 표(합 5.0s + 여유 1.0s = 턴 마감 6s, ADR 0020, 실측 안 함). 30s: 초기값, 근거 기록 없음
                 max_loading: int = MAX_LOADING, put_roles: frozenset | None = None, conn_timeout_s: float = CONN_TIMEOUT_S):
        self.runtime_dirs = [Path(d) for d in (runtime_dirs or [])]   # HOME·캐시 디렉터리(환경 변수로 정해진 것만)
        self.hf_home = Path(hf_home) if hf_home else None   # vllm 백엔드일 때만 받는다(모델 내려받기 캐시)
        self.test_hooks = dict(test_hooks or {})
        self.release_url, self.release_required, self.probe_ttl_s = release_url, release_required, probe_ttl_s
        self._probe = (0.0, None)   # (시각, 사유 또는 None)
        self.schema_dir, self.bundle_dir = Path(schema_dir), Path(bundle_dir)
        self.reader_factory, self.backend_name = reader_factory, backend_name
        self.load_session, self.probe, self.runtime_version, self.facts = load_session, probe, runtime_version, facts   # wiring이 주입한다
        self.tokens = dict(tokens or {})
        self.queue_wait_s, self.put_wait_s, self.max_releases = queue_wait_s, put_wait_s, max_releases
        self.releases: dict[str, Release] = {}      # 삽입 순서 = 적재 순서
        self.rlock = threading.Lock()                # 릴리스 표
        self.engine_lock = threading.Lock()          # 엔진 직렬화
        self.pending = 0                             # 실행 중 + 대기 중
        self.avg_s = 0.5                             # 호출 시간 지수 이동 평균
        self.deadlines = dict(DEFAULT_DEADLINES_S if deadlines is None else deadlines)   # 호출별 총 상한(op → 초)
        self.backend_retry_s = backend_retry_s   # 백엔드 기동 재시도 간격(0이면 재시도 없음). 출력 재시도와 무관하다
        self.max_input_chars, self.max_loading, self.conn_timeout_s = max_input_chars, max_loading, conn_timeout_s
        self.put_roles = None if put_roles is None else frozenset(put_roles)   # None이면 인증된 모든 역할이 적재 신호를 보낼 수 있다(survey-service가 404에서 PUT으로 다시 적재한다)
        self.overdue = False          # 상한을 넘긴 엔진 호출이 아직 끝나지 않았다(엔진이 멈췄을 수 있다)
        self.engine_error = None      # 마지막 추론이 엔진 예외로 끝났으면 예외 이름(성공하면 None)
        self._closing = False
        self.base = None
        self.ready, self.not_ready_reason = False, "백엔드를 올리는 중"
        self.bundle_error = dirs_check({"bundle_dir": str(self.bundle_dir), "hf_home": str(self.hf_home) if self.hf_home else None, "runtime_dirs": [str(d) for d in self.runtime_dirs]})["error"]
        if self.bundle_error:
            log.error("%s", self.bundle_error)
            self.not_ready_reason = self.bundle_error
            return   # 백엔드를 올리지 않는다. /readyz는 503과 이유를 낸다
        threading.Thread(target=self._start_backend, args=(base_backend_factory,), daemon=True).start()

    def readiness(self) -> tuple[bool, str | None]:
        """요청을 받을 수 있는가: 백엔드·번들 디렉터리·I4 서버 도달성. 사유에는 주소의 호스트:포트만 담는다(토큰·경로 없음).
        /healthz는 이것과 무관하게 프로세스가 살아 있으면 200이다."""
        if not self.ready:
            return False, self.not_ready_reason
        if self.overdue:
            return False, "추론 엔진이 요청 상한을 넘겨 응답하지 않는다"
        if self.engine_error:
            return False, f"추론 엔진 호출이 실패했다({self.engine_error})"
        if self.release_required and not self.release_url:
            return False, "MODEL_RUNTIME_RELEASE_URL이 설정되지 않았다"
        if self.release_url:
            t, why = self._probe
            if time.time() - t >= self.probe_ttl_s:
                why = self.probe(self.release_url)   # 도달 못 하면 사유(호스트:포트만), 닿으면 None
                self._probe = (time.time(), why)
            if why:
                return False, why
        return True, None

    def close(self):
        """종료 시 엔진을 내린다(시그널 처리에서 부른다). 내리지 않으면 EngineCore가 GPU 메모리를 쥔 채 남는다."""
        self.ready = False
        self._closing = True
        with self.rlock:
            self.releases.clear()
        close = getattr(self.base, "close", None)
        if close:
            close()

    def _start_backend(self, factory):
        while not self._closing:
            try:
                self.base = factory()
                self.ready, self.not_ready_reason = True, None
                return
            except Exception as e:   # noqa: BLE001
                self.not_ready_reason = type(e).__name__
                log.error("백엔드를 올리지 못함: %s", type(e).__name__)
            if self.backend_retry_s <= 0:
                return
            time.sleep(self.backend_retry_s)   # 모델 내려받기 실패 등은 돌아오면 사람 개입 없이 /readyz가 200이 된다(FAULT_POLICY 12)

    # --- 적재 ---
    def put(self, rid: str):
        """(상태 코드, 본문). 멱등: LOADED면 200, LOADING이면 대기 후 상태, FAILED·ABSENT면 새로 시도한다."""
        if not self.ready:
            return 503, BoundaryError("UNAVAILABLE", self.not_ready_reason or "서버가 아직 준비되지 않았다").to_record()
        if self.release_required and not self.release_url:
            return 503, BoundaryError("UNAVAILABLE", "MODEL_RUNTIME_RELEASE_URL이 설정되지 않았다").to_record()
        with self.rlock:
            r = self.releases.get(rid)
            if r is None or r.state == "FAILED":
                if sum(1 for v in self.releases.values() if v.state == "LOADING") >= self.max_loading:
                    return 503, BoundaryError("UNAVAILABLE", "적재가 몰려 있다. 잠시 후 다시 보낸다").to_record()
                self.releases.pop(rid, None)
                failed = [k for k, v in self.releases.items() if v.state == "FAILED"]
                for k in failed[:-(MAX_FAILED_KEPT - 1)] if len(failed) >= MAX_FAILED_KEPT else []:
                    del self.releases[k]   # 실패 항목이 무한히 쌓이지 않게 오래된 것부터 버린다
                r = self.releases[rid] = Release()
                threading.Thread(target=self._load, args=(rid, r), daemon=True).start()
        r.done.wait(self.put_wait_s)
        if r.state == "LOADED":
            return 200, self.status_body(rid, r)
        if r.state == "LOADING":
            return 202, self.status_body(rid, r)
        return STATUS[r.error.code], r.error.to_record()

    def _load(self, rid: str, r: Release):
        try:
            bundle = self.reader_factory().load_bundle(rid, dest=self.bundle_dir)
            m = bundle.manifest
            if m["runtime_contract_version"] != SUPPORTED_RUNTIME_CONTRACT_VERSION:
                raise BoundaryError("UNSUPPORTED_VERSION", "runtime_contract_version을 지원하지 않는다")
            if m["model_runtime_version"].split(".")[:2] != self.runtime_version.split(".")[:2]:
                raise BoundaryError("UNSUPPORTED_VERSION", "model_runtime_version의 MAJOR.MINOR가 이 서버와 다르다")
            cfgs = {k: m["adapters"][k]["serving_config"] for k in ("LORA_A", "LORA_B")}
            hs = {k: m["adapters"][k]["serving_config_hash"] for k in cfgs}
            prompts = {k: m["adapters"][k]["system_prompt"] for k in cfgs}   # 패키지의 시스템 프롬프트(파일은 어댑터 디렉터리 안, D75)
            backend = self.base.bind(bundle.adapters) if hasattr(self.base, "bind") else self.base   # 어댑터 사전 검사 포함
            session = self.load_session(bundle.adapters, cfgs, schema_dir=self.schema_dir, expected_serving_config_hashes=hs,
                           system_prompts=prompts, backend=backend)
            with self.rlock:
                r.session, r.hashes, r.state = session, dict(session.serving_config_hashes), "LOADED"
                r.prompt_hashes = dict(session.system_prompt_hashes)
                loaded = [k for k, v in self.releases.items() if v.state == "LOADED"]
                for k in loaded[:-self.max_releases] if len(loaded) > self.max_releases else []:
                    del self.releases[k]
                    self._drop_bundle(k)   # 내려간 릴리스의 번들 파일도 지운다(MR-9)
            log.info("적재 완료 release=%s", rid)
        except Exception as e:   # noqa: BLE001  실패는 이 릴리스에만 기록한다. 다른 릴리스는 그대로다
            r.error, r.state = to_boundary(e), "FAILED"
            self._drop_bundle(rid)
            log.warning("적재 실패 release=%s code=%s", rid, r.error.code)
        finally:
            r.done.set()

    def _drop_bundle(self, rid: str) -> None:
        """번들 디렉터리 아래의 릴리스 폴더를 지운다. ID 형식과 경로가 번들 디렉터리 바로 아래일 때만."""
        if violations("PutRequest", {"release_id": rid}):
            return
        d = self.bundle_dir / rid
        if d.parent == self.bundle_dir and d.is_dir() and not d.is_symlink():
            shutil.rmtree(d, ignore_errors=True)

    def status_body(self, rid: str, r: Release | None):
        if r is None:
            return {"release_id": rid, "state": "ABSENT", "error": None}
        return {"release_id": rid, "state": r.state, "error": r.error.to_record() if r.error else None}

    def get(self, rid: str):
        with self.rlock:
            return self.status_body(rid, self.releases.get(rid))

    def info(self):
        with self.rlock:
            loaded = [k for k, v in self.releases.items() if v.state == "LOADED"]
            hashes = self.releases[loaded[-1]].hashes if loaded else {}
            prompt_hashes = self.releases[loaded[-1]].prompt_hashes if loaded else {}
        base = self.base
        expect = None
        if base is not None:   # 매니페스트의 serving_config가 맞춰야 하는 값(deploy·model-lab이 참고한다)
            expect = {"constraint": {"backend": base.name, "backend_version": base.version,
                                     "supported_features": sorted(base.supported_features),
                                     "relax_policy_version": self.facts["relax_policy_version"]},
                      "prompt_template_versions": sorted(self.facts["template_versions"]),
                      "prompt_template_version_default": self.facts["template_version_default"],   # 새 매니페스트는 이 값을 쓴다(D77)
                      "deprecated_prompt_template_versions": sorted(self.facts["deprecated_template_versions"]),
                      "protection_policy_versions": sorted(self.facts["protection_policy_versions"]),
                      "output_contract": SUPPORTED_RUNTIME_CONTRACT_VERSION}
        return {"service": "model-runtime", "runtime_version": self.runtime_version,
                "supported_contract_versions": [SUPPORTED_RUNTIME_CONTRACT_VERSION], "backend": self.backend_name,
                "loaded": loaded, "serving_config_hash": hashes,   # 해시는 가장 최근에 적재한 릴리스의 것
                "system_prompt_sha256": prompt_hashes,   # 가장 최근에 적재한 릴리스의 패키지 프롬프트 해시(D75)
                "serving_config_expectation": expect, "test_hooks": self.test_hooks}

    # --- 추론 ---
    @staticmethod
    def split_extract_body(body):
        """extract 본문 ExtractRequest{input, flow, fields}를 (input, flow, fields)로 나눈다. 형식은 받는 쪽(link_receive)이 계약 스키마로 이미 검증했다(R7)."""
        return body["input"], body["flow"], body["fields"]

    def infer(self, rid: str, op: str, body):
        """op: extract | respond. (상태, 본문, 헤더). extract의 본문은 dict, respond의 본문은 문장(str). 대기가 길 것 같으면 503."""
        with self.rlock:
            r = self.releases.get(rid)
            if r is None or r.state != "LOADED":
                return 404, BoundaryError("NOT_FOUND", "적재되지 않은 릴리스").to_record(), {}
            if self.pending > 0 and (self.pending + 1) * self.avg_s > self.queue_wait_s:
                return 503, BoundaryError("UNAVAILABLE", "대기가 길어 거절한다").to_record(), {}
            self.pending += 1
        try:
            flow = fields = None
            if op == "extract":
                body, flow, fields = self.split_extract_body(body)   # 이후 body는 모델 입력(input)이다
            # 한도는 모델 입력에만 건다: flow는 프롬프트에 들어가지 않으므로 컨텍스트를 쓰지 않는다(2500자 근거는 ADR 0012)
            if len(json.dumps(body, ensure_ascii=False)) > self.max_input_chars:   # 프롬프트가 컨텍스트를 넘기지 않게 호출 전에 거절(MR-3)
                return 400, BoundaryError("INVALID_RECORD", f"입력이 너무 길다(최대 {self.max_input_chars}자)").to_record(), {}
            limit_s = self.deadlines[op]
            deadline = time.monotonic() + limit_s   # 호출 하나의 총 상한(대기 + 추론 + 재시도). 시도별이 아니라 총합이다
            if not self.engine_lock.acquire(timeout=max(0.0, min(self.queue_wait_s, deadline - time.monotonic()))):
                return 503, BoundaryError("UNAVAILABLE", "대기가 길어 거절한다").to_record(), {}
            res: dict = {}
            done = threading.Event()

            def work():   # 엔진 호출은 끝날 때까지 락을 쥔다. 호출자는 상한에서 포기하고, 엔진이 돌아오면 락이 풀린다
                try:
                    t = time.time()
                    out = r.session.extract(body, flow, fields, deadline=deadline) if op == "extract" else r.session.respond(body, deadline=deadline)   # 재시도는 남은 예산이 직전 시도 시간 이상일 때만(roles.run_attempts)
                    info = r.session.last_call_info
                    self.avg_s = 0.8 * self.avg_s + 0.2 * (time.time() - t)
                    res["ok"] = (out, info)
                except BaseException as e:   # noqa: BLE001
                    res["err"] = e
                finally:
                    self.engine_lock.release()
                    self.overdue = False
                    done.set()
            threading.Thread(target=work, daemon=True).start()
            if not done.wait(max(0.0, deadline - time.monotonic())):
                self.overdue = True
                log.error("추론이 호출 상한 %.1fs를 넘겼다(op=%s)", limit_s, op)
                return 503, BoundaryError("UNAVAILABLE", "추론이 요청 상한을 넘겼다", {"reason": "ENGINE_TIMEOUT"}).to_record(), {}
            if "err" in res:
                raise res["err"]
            out, info = res["ok"]
            self.engine_error = None
            headers = {"X-Openit-Outcome": info.outcome, "X-Openit-Attempts": str(info.attempts)}
            if info.protections:
                headers["X-Openit-Protections"] = ",".join(f"{k}:{v}" for k, v in info.protections)
            return 200, (out.text if op == "respond" else out), headers
        except ContractViolation as e:
            return 400, BoundaryError("INVALID_RECORD", str(e)).to_record(), {}
        except ModelOutputInvalid as e:   # D75: 값을 지어내지 않는다. 호출자(survey-service)가 한 번 다시 보낸다
            log.warning("OUTPUT_INVALID attempts=%s violations=%s", e.attempts, ",".join(violation_names(e.errors)))
            return 503, BoundaryError("UNAVAILABLE", "모델 출력이 ActionExtraction 형식을 지키지 못했다",
                                      {"reason": "OUTPUT_INVALID", "attempts": e.attempts,
                                       "violations": violation_names(e.errors)}).to_record(), {}
        except ValueError as e:   # 엔진이 입력(프롬프트 길이 등)을 거부: 요청 하나의 문제다. /readyz를 건드리지 않는다(MR-3)
            log.warning("엔진이 입력을 거부함 %s", type(e).__name__)
            return 400, BoundaryError("INVALID_RECORD", "입력을 처리할 수 없다(길이 등)").to_record(), {}
        except Exception as e:   # noqa: BLE001 (엔진 사망 등)
            log.error("추론 실패 %s", type(e).__name__)
            self.engine_error = type(e).__name__   # 엔진 사망 등: 다음 추론이 성공할 때까지 /readyz 503
            return 503, BoundaryError("UNAVAILABLE", "추론 실패").to_record(), {}
        finally:
            with self.rlock:
                self.pending -= 1
