# 0035 영역 게이트웨이 출구와 노출 선언(계약 3.4.4~3.4.6)

## 상태
채택(2026-11-04, 코드 단계 지시 1~8항).

## 맥락
계약 3.4.4(CONVENTIONS 13절, RUNTIME R1.8)부터 영역 밖으로 나가는 호출은 자기 영역 게이트웨이의 HTTP CONNECT 터널로만 나간다. 터널 주소는 공통 환경 변수 `OPENIT_EGRESS_PROXY`(CommonEnv)이고, 게이트웨이는 영역이 이미지 라벨 `io.openit.exposure`(Exposure)의 `egress`에 선언한 상대에게만 터널을 연다. 이 영역이 부르는 바깥은 release-gate(I4 번들 읽기) 하나다. 도달성 확인(`/healthz` GET)은 계약 클라이언트를 거치지 않는 직접 호출이라 따로 같은 출구로 보내야 한다.

## 결정
1. 번들 읽기 클라이언트(`HttpReleaseReader`)에 `egress_proxy=egress_proxy_from(environ)`을 넘긴다. 환경을 통째로 넘기지 않고 이름 하나만 꺼낸다(R9.1). 구현: `components/link_release_fetch.py`.
2. 도달성 확인도 같은 값으로만 나간다. `urllib` 오프너를 `ProxyHandler({"https": 출구})`(출구가 없으면 `{}`)로 만들어 `HTTPS_PROXY` 등 환경 변수의 프록시는 읽지 않는다(계약 클라이언트와 같은 규칙).
3. `OPENIT_EGRESS_PROXY`가 loopback `http://`가 아니면 시작을 거부한다(`egress_proxy_from`이 `ValueError`, 서버는 `SystemExit`로 이름만 알린다).
4. 노출 선언의 정본은 리포의 `deploy-exposure.json`이고 `scripts/build-image.sh`가 이미지에 라벨로 달기 전에 계약 `Exposure`로 검증한다. `egress: ["release-gate"]`, 경로는 모두 `denied`(이 서버는 platform 공개 입구로 노출하지 않는다. survey-service는 자기 영역 게이트웨이에서 이 영역 게이트웨이를 거쳐 부른다).
5. 잡 선언 `io.openit.jobs`는 달지 않는다. 이 영역에는 주기 잡이 없다(서버 한 프로세스와 `setup-env.sh`뿐). `JobsDeclaration`은 `jobs`가 1개 이상이어야 하므로 빈 선언은 계약 위반이고, ImageLabels는 "없으면 잡 없음"이라고 정한다.
6. 경로 본문 상한 `max_body_bytes: 4194304`(4MiB)를 선언한다(계약 3.4.7). 서버 자신의 상한 `MAX_BODY`(link_receive, ADR 0012)와 같은 값이어서 게이트웨이와 앱이 같은 413을 낸다. 4MiB는 근거 기록 없음·측정 안 함(ADR 0012의 값을 그대로 옮겼다). 실제 입력은 `MAX_INPUT_CHARS`가 훨씬 작게 막는다.
7. 데이터베이스는 쓰지 않는다. 적재 상태는 프로세스 메모리이고 재시작을 넘지 않는다(R4.6). 그래서 백업 잡과 `OPENIT_BACKUP_*`는 해당 없다.

## 왜
- 표준: HTTP CONNECT 터널(RFC 9110 9.3.6)로 TLS를 끝까지 유지한다. 계약 R1.8이 정한다.
- 환경 변수 프록시(`HTTPS_PROXY`)를 읽지 않는 이유: 영역 게이트웨이가 아닌 곳으로 새는 길을 없애기 위해서다(계약 클라이언트도 같은 규칙).
- 라벨 검증을 내보내기 전에 하는 이유: 생산자 출력은 생산자가 계약으로 검증한다(CONVENTIONS 3절, ADR 0010 R7).

## 검토한 대안과 버리는 이유
- 도달성 확인만 직접 연결로 두기: 클라이언트를 거치지 않는 바깥 호출도 같은 출구로만 나가야 하므로(코드 단계 지시 3항) 기각.
- 빈 `JobsDeclaration` 라벨: 스키마 `minItems: 1` 위반이라 기각.

## 결과
얻는 것: 게이트웨이 허용 목록과 코드의 호출 상대가 같은 선언(`egress`)에서 나온다. 감수하는 것: 출구 없이(개발) 직접 나가는 경로가 남는다. 운영에서 `OPENIT_EGRESS_PROXY`를 주는 것은 deploy 몫이고, 이 영역은 값이 없을 때 막지 않는다(ModelServingEnv에 필수로 선언되지 않았다). 실제 게이트웨이를 거친 호출은 이 영역 시험에서 확인하지 않았고(CONNECT 자리표시 서버로만 확인), deploy E2E 몫이다.

## 되돌리는 조건
계약이 출구 규칙이나 `Exposure.egress`를 바꾸면 새 ADR로 대체한다.
