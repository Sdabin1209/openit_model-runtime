# ADR 0029: 시계를 주입한다 (U422)

## 상태
채택(2026-10-31, 헤드 지시 U422).

## 맥락
호출 마감·대기 상한·백엔드 기동 재시도 간격·`/readyz` 도달성 캐시·종료 유예 같은 우리 코드의 시간 규칙을 시험이 실제 시간으로 재거나 기다렸다(`assertLess(elapsed, N)`, `time.sleep`, 실제 SIGTERM). 기계가 바쁘면 결과가 바뀌어 통과해도 옳다는 뜻이 아니고 실패해도 결함이 아니었다. 또 `test_server_properties`의 서브프로세스 시험은 지연(DELAY)이 실제로는 적용되지 않아 공허했다.

## 결정
1. `release_registry`와 `retry_loop`는 `time`을 읽거나 기다리지 않고 **시계를 인자로** 받는다. 시계의 모양은 `monotonic()`, `time()`, `sleep(s)`, `wait(event, timeout)`, `acquire(lock, timeout)`다. 실제 시계는 `wiring/clock.py`(`RealClock`) 하나이고 `wiring/service.py`가 기본으로 넣는다. 서버 종료의 유예 대기는 `Service.drain(grace_s)`로 옮겨 같은 시계를 쓴다.
2. 시험은 `tests/fake_clock.py`의 가짜 시계를 넣는다. 시간은 시험이 `advance()`할 때만 흐르고, `wait`·`acquire`·`sleep`은 조건이 참이 되거나 가짜 시간이 마감에 닿을 때까지 돌아오지 않는다.
3. waitress 연결 시한, 연결 상한, SIGTERM 처리 시간은 라이브러리·운영체제의 동작이라 시험하지 않는다. 우리가 넘기는 값(`channel_timeout`, `threads`, `connection_limit`, `cleanup_interval`)과 종료 처리기(`main()`이 거는 처리기, 서버를 닫고 진행 중 호출을 기다린 뒤 엔진을 내리는 순서)를 확인한다.
4. 와이어 형식, HTTP 응답, 이미지 라벨은 바꾸지 않는다. 같은 기존 시험(상태 코드, 오류 본문, 헤더)이 가짜 시계 위에서 그대로 통과한다.

## 왜
의존성 주입(포트·어댑터)은 DESIGN.md 2절의 방식이다. 시험이 시간을 직접 흘리면 "9초 지남"을 일으켜 결과(시간 초과 상태, 오류 종류, 재시도 여부)만 판정하므로 부하와 무관하다.

## 검토한 대안과 버리는 이유
- 전역 `time`을 monkeypatch하거나 freezegun: `Event.wait(timeout)`·`Lock.acquire(timeout)`·소켓 timeout 같은 블로킹 대기는 `time` 함수를 바꿔도 움직이지 않는다. 그 대기가 바로 이 영역의 마감(호출 상한, PUT 대기)이다.
- 대기 상한을 늘려 흔들림을 줄이기(U420): 덜 깨질 뿐 판정이 시간에 달린 채로 남는다.
- 서브프로세스로 실제 서버를 띄워 신호·지연을 재기: 라이브러리와 운영체제를 시험하는 것이고 smoke·E2E의 몫이다.

## 결과
얻는 것: 시험이 실제로 기다리는 시간 0(영역 시험 3초 안팎), 부하와 무관한 결과. 감수하는 것: 시계 인자가 컴포넌트 생성자에 늘었고, 가짜 시계의 폴링 간격(1ms)이 시험 보조에 남는다(tests/TIMING.md). 실제 waitress가 시한에 연결을 닫는지, 실제 SIGTERM에서 진행 중 요청이 끝나는지는 이 시험들이 더 이상 보지 않는다(smoke·E2E의 몫).

## 되돌리는 조건
시계 주입이 컴포넌트 시그니처를 어렵게 하면 `Clock` 포트를 하나의 객체로 묶는 지금 모양을 유지한 채 구현만 바꾼다(되돌리지 않는다).
