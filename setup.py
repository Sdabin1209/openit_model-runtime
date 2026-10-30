"""패키지 탐색. 목록을 손으로 쓰면 새 패키지(예: prompts, schema_guard)가 wheel에서 빠진다(0.11.4). 그래서 src/ 아래를 찾아 낸다.
스키마의 정본은 리포 루트 schemas/(DESIGN.md 3절)이다. 컴포넌트가 실행 중에 읽어야 하므로 설치물에는 model_runtime.schemas로 같은 파일을 넣는다(리포에 복사본을 두지 않는다).
빌드 설정의 나머지는 pyproject.toml."""
from setuptools import find_packages, setup

setup(
    packages=find_packages("src") + ["model_runtime.schemas", "model_runtime.schemas.examples"],
    package_dir={"": "src", "model_runtime.schemas": "schemas", "model_runtime.schemas.examples": "schemas/examples"},
)
