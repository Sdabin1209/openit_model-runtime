# CR-007: openit_link가 깊게 중첩된 JSON에서 500을 낸다 (대상: link, 헤드)

- 무엇: `openit_link.server.App.handle`은 본문을 `json.loads`로 읽고 `ValueError`만 잡는다. 깊게 중첩된 JSON(수만 겹의 `[`)은 `RecursionError`를 내서 앱 밖으로 새고 서버가 500을 낸다. 계약은 JSON이 아닌 본문에 400 INVALID_RECORD를 정한다.
- 영향: model-runtime은 기존 시험(`tests/test_hardening.py::Resources::test_malformed_bodies`)이 이것을 요구한다. 지금은 `components/link_receive.py`의 `_guard`가 대신 막는다.
- 요청: `except (ValueError, RecursionError)`로 바꾼다. 반영되면 `_guard`를 지운다.
- 요청 2: `/readyz`가 인증 없이 사유를 줄 수 없어 model-runtime은 사유를 로그로만 낸다(이전에는 인증된 호출자에게 응답에 실었다). 인증된 호출자에게만 사유를 주는 방식을 라이브러리가 지원할지 정해 달라.
