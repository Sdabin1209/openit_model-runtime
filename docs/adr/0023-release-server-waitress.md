# ADR 0023: GPU 세션 키트의 I4 읽기 서버(release_server.py)를 WSGI 앱 + waitress로

## 상태
채택(2026-10-29, 헤드 지시). [ADR 0022](0022-wsgi-waitress.md)(추론 서버)와 같은 방식을 키트의 보조 서버에 적용한다. [ADR 0013](0013-gpu-session-kit.md) 8항("최소 읽기 서버")의 구현 방식을 바꾼다.

## 맥락
`scripts/gpu_session/release_server.py`는 GPU 세션에서 `prepare_bundle.py`가 만든 릴리스 디렉터리를 I4 모양으로 내주는 루프백 전용 읽기 서버다(`run_session.sh`가 띄운다). 표준 라이브러리 `ThreadingHTTPServer`를 직접 썼고, 종료 신호를 처리하지 않아 SIGTERM에 -15로 죽었다.

## 결정
1. `make_app(root)`(WSGI, PEP 3333)와 waitress 3.0.2로 바꾼다. `/healthz`, `GET /v1/releases/{id}`, `GET /v1/releases/{id}/bundle`의 응답은 그대로다.
2. 번들 tar는 스레드가 파이프에 쓰고 응답 이터러블이 읽어 내보내는 스트리밍이다(어댑터 약 350MB를 메모리에 올리지 않는다는 기존 성질 유지). 길이를 미리 알 수 없어 HTTP/1.1에서는 chunked로 나간다(기존의 `Connection: close` 스트림에서 바뀜. 계약의 `HttpReleaseReader`로 적재되는 것을 시험으로 확인했다).
3. 설정: `threads=4`, `connection_limit=8`, `channel_timeout=120`, `cleanup_interval=1`. 값의 출처: 클라이언트가 같은 호스트의 추론 서버 하나뿐이라는 **추정**이고 **측정하지 않았다**. 120은 추론 서버의 번들 읽기 timeout 기본값과 맞춘 **제약**이다. cleanup_interval은 ADR 0022와 같다.
4. 우아한 종료는 ADR 0022와 같다: SIGTERM/SIGINT에서 `SystemExit`을 올려 waitress `run()`이 진행 중 요청을 마치고 돌아오게 한다. 종료 코드 0.
5. 의존: waitress는 추론 서버와 같은 환경에 이미 있어야 한다(`requirements-*.lock`, `pod_setup.sh`가 이 잠금 파일로 설치). 키트의 PYTHONPATH 방식에서도 추론 서버 자체가 waitress를 import하므로 이 서버가 새로 요구하는 의존은 없다.

## 왜
D111(직접 만든 HTTP 서버를 표준 도구로)과 ADR 0022의 근거를 따른다. 대안(현 상태 유지)은 표준 점검의 O 항목을 키트에 남긴다. 기각.

## 결과
얻는 것: 서버 코드가 앱 로직만 남고 종료 신호를 처리한다. 감수하는 것: 번들 응답이 chunked로 바뀐다. GPU 세션 전체 실행(`run_session.sh`)은 GPU가 필요해 하지 않았다. 계약 클라이언트로 적재하는 시험만 했다.
