# STATUS

갱신: 2026-10-15 (6차)

## 한 일
- 6차: 첫 실제 백엔드 `VLLMBackend`(vLLM + 구조화 출력 + LoRA)를 `ConstraintBackend` Protocol 뒤에 구현했다. vLLM은 선택 의존(`.[vllm]`)이고 지연 import라 없으면 `BackendNotAvailable`이다. FakeBackend와 보호 경로는 그대로다. 12GB 값은 `profiles/gpu-dev-12g.json`.
- 단위 시험 81개 통과(`.venv/bin/python -m pytest`): 기존 67 + vLLM 모킹 14(요청 조립: 스키마 완화, 시드 = 기본 + 시도, 온도, 역할별 LoRA 선택, 역할별 설정, 기반 모델 추출, 구버전 API 이름).
- GPU 시험을 gpu-dev에서 실제로 돌렸다(헤드가 pip를 설치한 뒤). 결과는 design.md §11-R. 요약: LoRA 두 개 한 기반 위에서 요청별 선택 ○, LoRA+구조화 출력 동시 ○, `allOf`·`if/then`·`not`은 조용히 무시, `uniqueItems`는 거부, 나머지 시험한 키워드는 지켜짐, 시드 재현 ○(같은 서버), 서버 재시작 간 완전 일치는 보장 안 됨, VRAM 증가분(어댑터 둘) 약 0.07 GiB. vLLM을 첫 백엔드로 확정.
- 스크립트: `scripts/gpu_check.sh`(preflight/sync/setup/run/fetch/all, flock, 분리 실행, SSH 재시도 한 번), `gpu_probe.py`, `gpu_report.py`, `make_tiny_lora.py`. 런북에 절차.
- 변경 요청 002(스키마 규칙이 제약으로 안 지켜짐, 어댑터 순위 초과 시 엔진 사망, 버전 고정, 재현성).
- design.md에 "큰 스테이션에서 재확인" 목록.

## 남은 일
- 학습된 어댑터가 나오면 시나리오 적합성 표(§11-10)와 보호 개입 빈도를 실제 백엔드로 재실행.
- 공백 무한 생성 억제 설정(xgrammar 공백 옵션) 검토. 무작위 어댑터에서 파서 출력이 닫히지 않는 경우가 많았다.
- `RUNTIME_VERSION`/패키지 버전은 올리지 않았다(0.3.0 미배포 상태에 추가). 헤드가 정한다.
- 이미지 빌드(Dockerfile)는 이 영역이 서비스를 갖지 않아 해당 없음. 호출하는 서비스 이미지가 `.[vllm]`과 `VLLM_USE_FLASHINFER_SAMPLER` 설정 여부를 정해야 한다.
- 사용자(도메인 소유자) 확정 대기: 대체 문장의 마지막 상수 문구(`protect-1` 잠정).
- 이 리포지토리 Git 태그 설치 경로 확인(태그 없음). push는 하지 않았다.

## 막힌 것
- 없음. (GPU 호스트 사용 규칙 D47을 지켰다: flock, 작업 폴더 `~/openit-gpu/model-runtime/`, sudo·비밀 값 없음. 호스트 쓰기는 작업 폴더와 모델 캐시뿐.)

## 알려야 할 것
- `serving_config` 형식 변경 없음. vLLM을 쓰려면 `constraint`에 `backend: "vllm"`, 정확한 `backend_version`, `DEFAULT_SUPPORTED_FEATURES`가 필요하다.
