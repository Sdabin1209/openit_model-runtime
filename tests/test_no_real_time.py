"""시험과 컴포넌트에서 실제 시간을 읽거나 기다려 판정하는 곳이 없음을 정적으로 강제한다(U422, ADR 0029).

시험 코드: time.monotonic/perf_counter/time.time/time.sleep, settimeout, 숫자 timeout/wait/join 금지(매달림 감시 HANG_GUARD_SECONDS 하나만).
예외 파일(tests/TIMING.md 표에 이유): fake_clock.py(가짜 시계·폴링 간격), helpers.py(조건 대기 폴링), stub_oom_server.py(영원히 막는 가짜 서버).
동작 코드: 실제 시간은 wiring/clock.py만 읽는다. 컴포넌트·wiring·진입점은 시계를 인자로 받는다."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_OK = {"fake_clock.py", "helpers.py", "stub_oom_server.py", "test_no_real_time.py"}
TEST_PATTERN = re.compile(r"time\.monotonic|perf_counter|time\.time\(|time\.sleep|settimeout|\.wait\(\s*[0-9]|\.join\(\s*[0-9]|timeout\s*=\s*[0-9]|(?<![A-Za-z_.])sleep\(")
SRC_PATTERN = re.compile(r"\btime\.(monotonic|time|sleep|perf_counter)\b|datetime\.(now|utcnow)|\bimport time\b|from time import")


def code(path: Path):
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield n, line.split("#", 1)[0]   # 주석은 본다고 치지 않는다


class NoRealTime(unittest.TestCase):
    def test_tests_do_not_read_or_wait_on_real_time(self):
        for f in sorted((ROOT / "tests").glob("*.py")):
            if f.name in TESTS_OK:
                continue
            for n, line in code(f):
                self.assertIsNone(TEST_PATTERN.search(line), f"{f.name}:{n}: {line.strip()}")

    def test_only_the_real_clock_reads_time_in_the_package(self):
        for f in sorted((ROOT / "src" / "model_runtime").rglob("*.py")):
            if f.name == "clock.py" and f.parent.name == "wiring":
                continue
            for n, line in code(f):
                self.assertIsNone(SRC_PATTERN.search(line), f"{f.relative_to(ROOT)}:{n}: {line.strip()}")

    def test_timing_table_lists_the_exceptions(self):
        t = (ROOT / "tests" / "TIMING.md").read_text(encoding="utf-8")
        for name in TESTS_OK - {"test_no_real_time.py"}:
            self.assertIn(name, t)


if __name__ == "__main__":
    unittest.main()
