# ADR 0010: HTTP 서버는 표준 라이브러리(ThreadingHTTPServer)

## 상태
대체됨(0.10.0, [ADR 0022](0022-wsgi-waitress.md)). 채택(0.5~, 변경 요청 003). 선택 당시의 비교는 '근거 기록 없음'.

## 맥락
I9의 서버 경로는 몇 개뿐이다(PUT/GET 릴리스, extract, respond, info, healthz, readyz). 이 서버는 엔진 직렬 호출이 병목이고 입력이 작다.

## 결정
`http.server.ThreadingHTTPServer`를 쓰고, 동시 연결 상한을 둔 `BoundedServer`로 감싼다(ADR 0012). 의존은 `jsonschema`와 `boundary-contracts`뿐이다(`pyproject.toml`).

## 왜 (따른 표준과 근거)
확인되는 사실: design.md 8절이 '표준 라이브러리 HTTP'라고 적었을 뿐 FastAPI 등과의 비교 기록은 없다. 코드에서 보이는 결과적 이점: 서버 이미지 의존이 늘지 않고(`requirements-*.lock` 외에 웹 프레임워크 없음), 경계 계약 클라이언트(`boundary_contracts.clients`, urllib)와 같은 표준 라이브러리 계열이다. 그러나 이것이 선택 당시의 이유였다는 기록은 **근거 기록 없음.**

## 검토한 대안과 버리는 이유
- FastAPI(+uvicorn) 같은 프레임워크: 비동기·자동 검증·성능 면에서 유리할 수 있으나 이 리포에서 비교·측정하지 않았다(근거 기록 없음).

## 결과
감수하는 것: 스레드당 연결(상한 32), 느린 클라이언트 방어를 직접 한다(소켓 timeout, 본문·JSON 검증은 ADR 0012), HTTP/1.1 keep-alive만, 파싱 취약점 대응은 파이썬 표준 라이브러리 업데이트에 의존. 성능은 측정하지 않았다.

## 되돌리는 조건
동시 요청 처리량 요구가 생기거나(ADR 0005 되돌림) 직접 구현한 방어가 부담이 되면 프레임워크로 바꾸는 것을 비교해 새 ADR로 한다.
