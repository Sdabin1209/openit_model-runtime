# ADR 0001: 추론 엔진으로 vLLM을 쓴다

## 상태
채택(2026-10-15 제안, gpu-dev 시험으로 확정: design.md 11-R). 대안 비교의 근거는 일부 '근거 기록 없음'.

## 맥락
학습 후 평가, 게이트의 승인 평가, 서비스 추론이 같은 방식으로 모델을 로딩하고 호출해야 한다(brief 1절). 서비스는 한 기반 모델에 LoRA-A·LoRA-B 두 어댑터를 쓰고, 출력은 JSON 스키마를 지켜야 하며, 재시도 시드 규칙(기본 시드 + 시도 번호)이 있다. 엔진 선택은 model-runtime이 정한다(INTERFACES I9, O14).

## 결정
백엔드는 `ConstraintBackend` Protocol 뒤에 두고, 첫 실제 구현으로 vLLM(프로세스 안 엔진, `vllm_backend.py`)을 쓴다. vLLM은 선택 의존(`.[vllm]`)이고 지연 import라 없으면 `BackendNotAvailable`이다.

## 왜 (따른 표준과 근거)
요구사항 세 가지를 vLLM이 충족한다고 gpu-dev에서 확인했다(design.md 2.1 근거와 11-R (e)): (1) `load`가 어댑터 디렉터리 경로를 받으므로 경로로 LoRA를 요청별로 붙일 수 있어야 한다. (2) JSON 스키마 제약 디코딩과 요청별 시드를 지원해야 한다. (3) 서비스의 추론 서버와 같은 계열이면 평가와 서비스가 같은 디코딩 경로를 쓰기 쉽다. LoRA와 구조화 출력 동시 사용도 gpu-dev(vLLM 0.31.0, xgrammar 0.2.7)에서 확인했다. 따른 표준: 없음(엔진은 표준이 아니라 요구사항 충족으로 고른다). 버전은 `requirements-gpu.lock`으로 고정한다(공통 규칙).

## 검토한 대안과 버리는 이유
- TGI, llama.cpp, transformers 직접 호출: **근거 기록 없음.** 이 리포의 문서와 git log에는 이 세 가지를 위 요구사항으로 비교한 기록이 없다. design.md 2.1의 근거는 "사전 지식"이라고 스스로 적고 있고, 이 ADR도 대안이 요구사항을 못 채운다고 주장할 근거를 갖고 있지 않다. 비교가 필요해지면 새 ADR로 한다.
- 엔진 교체는 `ConstraintBackend` 구현 하나를 바꾸는 일로 한정하도록 설계했다(검증·재시도는 엔진과 무관).

## 결과
얻는 것: 요구한 세 기능을 한 엔진으로 해결, 실측(gpu-dev 12GB, 약 10.1GB 사용). 감수하는 것: vLLM·xgrammar 버전에 `supported_features`와 문법 동작이 묶인다(ADR 0002). 24GB·Qwen3-8B 적재는 측정하지 않았다(ADR 0008).

## 되돌리는 조건
vLLM이 위 세 요구 중 하나를 못 채우게 되거나(새 버전의 LoRA·구조화 출력 동시 사용 깨짐 등), 다른 엔진이 같은 요구를 측정으로 더 잘 채울 때. 그때는 새 `ConstraintBackend`를 만들고 `serving_config.constraint.backend`(이름·버전)가 해시에 반영되므로 매니페스트를 다시 낸다.
