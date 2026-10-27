"""최종 학습 데이터(C1)와 서빙의 대조(선택 시험). MODEL_RUNTIME_REFERENCE_DATA_DIR(lora_{a,b}_{train,val,test}.jsonl이 든 읽기 전용 폴더)가 없으면 건너뛴다.
데이터는 리포에 없다. 전체 대조는 scripts/check_reference_data.py. 여기서는 분할마다 앞 300행으로 빠르게 확인한다."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

D = os.environ.get("MODEL_RUNTIME_REFERENCE_DATA_DIR")


@unittest.skipUnless(D and Path(D).is_dir() and list(Path(D).glob("lora_a_*.jsonl")), "MODEL_RUNTIME_REFERENCE_DATA_DIR 없음")
class ReferenceData(unittest.TestCase):
    def test_serving_reproduces_training_prompts_and_accepts_answers_within_input_limit(self):
        import check_reference_data as c
        from model_runtime import server
        res = c.run(Path(D), ["val", "test", "train"], 300, {})
        for role, r in res.items():
            self.assertGreater(r["rows"], 0)
            self.assertEqual(r["render_match"], r["rows"], (role, r["render_mismatch_causes"]))   # 글자 단위 재현
            self.assertEqual(len(r["system_prompt_kinds"]), 1, role)
            self.assertEqual(r["assistant"].get("strict_ok"), r["rows"], (role, r["assistant"]))    # 정답을 사후 검증기가 거부하지 않는다
            if role == "LORA_A":
                self.assertEqual(r["assistant"].get("relaxed_ok"), r["rows"])                      # 제약 디코딩 문법도 정답을 허용
                self.assertEqual(r["assistant"].get("key_order_ok"), r["rows"])
            self.assertEqual(r["over_max_input_chars"], 0, (role, r["user_len"]))                   # 정상 입력이 400이 되지 않는다
            self.assertLess(r["user_len"]["max"], server.MAX_INPUT_CHARS)
            self.assertEqual(r["rows_changed_by_special_token_neutralization"], 0)


if __name__ == "__main__":
    unittest.main()
