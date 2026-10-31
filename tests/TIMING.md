# 시험 중 시간 (U422, ADR 0029)

규칙: 시험 코드는 실제 시간을 읽거나 기다려서 판정하지 않는다. `tests/test_no_real_time.py`가 정적으로 강제한다(시험의 `time.monotonic/perf_counter/time.time/time.sleep`, `settimeout`, 숫자 `timeout=`·`.wait(숫자)`·`.join(숫자)` 금지). 동작 코드에서 실제 시간을 읽는 곳은 `wiring/clock.py` 하나다(컴포넌트는 시계를 인자로 받는다).
매달림 감시 마감은 `tests/fake_clock.py`의 `HANG_GUARD_SECONDS = 300` 하나다(정상 경로는 밀리초라 정상의 수천 배 이상, 합격·불합격의 기준이 아니라 코드가 진짜 멈췄을 때 시험이 끝나게 하는 장치).

처리 구분: 지움 / 1(우리 코드의 시간 -> 가짜 시계 주입) / 2(라이브러리 시간 -> 넘긴 값 확인 + 즉시 실패 가짜) / 3(속도 측정 -> 시간 단언 삭제) / 매달림 감시.

| 시험(파일::이름) | 확인하려던 요구사항 | 처리 | 바뀐/대체 시험 |
| --- | --- | --- | --- |
| test_deadline_budget::test_extract_over_limit_gives_engine_timeout_within_3s_default | extract 호출 상한이 지나면 503 ENGINE_TIMEOUT | 1 | test_extract_over_limit_gives_engine_timeout (엔진을 Event로 영원히 붙잡고 가짜 시계로 상한만큼 흘림, 걸린 시간은 재지 않음) |
| test_deadline_budget::test_respond_over_limit_gives_engine_timeout_within_2s_default | respond 호출 상한이 지나면 503 ENGINE_TIMEOUT | 1 | test_respond_over_limit_gives_engine_timeout |
| test_deadline_budget::기본 상한 값 3.0/2.0 | 계약 표의 상한 값 | 설정 값 확인 | test_contract_table_defaults, test_server_settings::test_env_reaches_the_service, test_deadlines_sum_fits_the_turn_deadline (합은 재지 않고 값을 비교) |
| test_deadline_budget::재시도 예산(retry 중단) | 남은 예산이 직전 시도보다 짧으면 재시도하지 않음 | 1 | RetryBudgetWithInjectedClock 4건(retry_loop에 가짜 시계 주입) |
| test_server::test_hung_engine_returns_503_engine_timeout_within_deadline_and_recovers | 멈춘 엔진: 상한에서 503, 다음 요청은 대기 상한에서 거절, /readyz 503, 복구 | 1 | 같은 이름(가짜 시계로 0.5초, 0.3초를 흘림) |
| test_server::test_hung_release_server_during_load_keeps_active_release | 번들 읽기가 멈춰도 PUT은 202, 활성 릴리스는 그대로 | 1 | 같은 이름(가짜 시계로 PUT 대기 0.3초를 흘림) |
| test_server::test_slow_load_returns_202_then_loaded | 적재가 PUT 대기보다 길면 202 LOADING 후 LOADED | 1 | 같은 이름(Event로 적재를 붙잡음) |
| test_server::test_backend_start_failure_reports_reason_then_recovers | 백엔드 기동 실패는 사유와 함께 재시도 간격마다 다시 시도해 복구 | 1 | 같은 이름(가짜 시계로 간격을 흘림) |
| test_server::test_slow_engine_within_deadline_is_normal | 상한 안의 느린 엔진은 정상 | 지움 | 정상 경로는 다른 모든 추론 시험이 이미 확인한다 |
| test_server::test_retries_are_bounded_by_the_total_deadline | 재시도가 총 상한을 넘기지 않음 | 지움(1로 이동) | RetryBudgetWithInjectedClock |
| (신규) test_server::test_healthz_does_not_queue_behind_a_busy_engine | 엔진이 바빠도 /healthz·/readyz는 줄 서지 않음 | 1(순서로 판정) | 엔진을 Event로 붙잡고 다른 요청이 끝남을 확인 |
| test_hardening::test_sigterm_exits_cleanly | SIGTERM에 우아하게 종료(코드 0) | 2, 지움 | test_server_settings::Shutdown(처리기가 걸리고 종료 코드 0, 닫는 순서), drain 2건(가짜 시계) |
| test_hardening::test_idle_connection_is_dropped_by_socket_timeout | 유휴 연결을 서버가 닫음 | 2, 지움(waitress 동작) | test_server_settings::test_values_passed_to_waitress(channel_timeout, cleanup_interval) |
| test_hardening::test_connection_cap_queues_extra_connection_until_a_slot_frees | 연결 상한 넘는 연결은 대기 | 2, 지움(waitress 동작) | 같은 시험(threads·connection_limit 값) |
| test_hardening::test_concurrent_loads_are_capped_and_failed_entries_pruned | 동시 적재 상한, 실패 항목 정리 | 1 | 같은 이름(PUT 대기를 가짜 시계로) |
| test_server_properties::test_slow_clients_do_not_block_other_requests | 느린 연결이 다른 요청을 막지 않음 | 2, 지움(waitress 스레드) | test_values_passed_to_waitress |
| test_server_properties::test_idle_connection_is_closed_by_the_server | 유휴 연결 닫힘 | 2, 지움 | test_values_passed_to_waitress |
| test_server_properties::test_sigterm_finishes_the_inflight_request_and_exits_zero | 종료 신호 때 진행 중 요청을 마치고 코드 0 | 2, 지움 | Shutdown::test_signal_handlers..., drain 2건 |
| test_server_properties::test_healthz_answers_while_the_engine_is_busy | 엔진이 바빠도 /healthz 응답 | 1, 지움 | test_server::test_healthz_does_not_queue_behind_a_busy_engine(이 시험은 서브프로세스 DELAY가 실제로는 적용되지 않던 시험이었다) |
| test_release_server::test_sigterm_exits_zero | GPU 세션 릴리스 서버가 SIGTERM에 코드 0 | 2, 지움 | test_waitress_settings_and_terminate_handlers(처리기와 waitress 값 확인) |
| test_release_server / test_gpu_session의 서브프로세스 대기 상한 | 서브프로세스가 끝나기를 기다림 | 매달림 감시 | `timeout=HANG_GUARD_S` |
| test_gpu_session::test_run_session_* (run_session.sh + stub_oom_server) | 메모리 부족 때 util을 낮춰 다시 시도 | 판정은 시간과 무관(결과 JSON). 셸 스크립트의 폴링 sleep 때문에 실제로 몇 초 기다린다 | 남김. 아래 "남은 것" |

## 이 표 밖에서 시간을 읽는 시험 보조 (예외)
- `tests/fake_clock.py`: 가짜 시계. 시간은 `advance()`가 부를 때만 흐른다. 블로킹 대기는 `_TICK.wait(0.001)`로 Event 설정을 알아채는 폴링이며 판정에 쓰이지 않는다(Event.set은 시계에 알리지 않는다).
- `tests/helpers.py::wait_until`: 조건 대기. 폴링 횟수로 세므로 실제 시계를 읽지 않는다. 마감은 매달림 감시.
- `tests/stub_oom_server.py`: GPU 세션 키트의 시험용 가짜 서버가 "영원히 준비되지 않음"을 흉내 내려고 `time.sleep(600)`한다(자기 가짜의 영원히 막기).

## 남은 것 (실제 시간이 남아 있는 곳과 이유)
- `test_gpu_session::test_run_session_*`: 셸 스크립트 `scripts/gpu_session/run_session.sh`가 서버 준비를 폴링하며 실제로 sleep한다. 합격·불합격은 시간이 아니라 결과 파일이며 마감은 매달림 감시지만, 몇 초 기다린다. 시계 주입은 셸 스크립트에 할 수 없어 못 했다. 헤드가 판단: GPU 세션 키트 점검(scripts/gpu_check.sh)의 몫으로 옮길지.
- 지운 시험 중 smoke·E2E가 봐야 할 것: (1) 실제 waitress가 느린 연결을 닫는지 (2) 실제 SIGTERM에서 진행 중 요청을 마치는지. 지금은 넘긴 값과 처리기 호출 순서만 확인한다.
