import json
import tempfile
import unittest
from pathlib import Path

import benchmark


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = benchmark.load_cases()

    def test_all_cases_validate(self):
        self.assertEqual([], benchmark.validate_all(self.cases))

    def test_public_case_does_not_expose_hidden_fields(self):
        hidden = {"oracle_facts", "key_unknowns", "utility", "hypotheses", "leakage_terms"}
        for case in self.cases.values():
            self.assertTrue(hidden.isdisjoint(benchmark.public_case(case)))

    def test_oracle_reveals_at_most_one_new_fact(self):
        for case in self.cases.values():
            first_fact = case["oracle_facts"][0]
            question = f"请说明{first_fact['triggers'][0]}方面的数据"
            fact_id, answer = benchmark.answer_question(case, question, set())
            self.assertIsNotNone(fact_id)
            self.assertTrue(answer)
            next_fact_id, _ = benchmark.answer_question(case, question, {fact_id})
            self.assertNotEqual(fact_id, next_fact_id)

    def test_unknown_question_returns_no_fact(self):
        case = next(iter(self.cases.values()))
        fact_id, answer = benchmark.answer_question(
            case, "月球背面的紫色独角兽有多少只", set()
        )
        self.assertIsNone(fact_id)
        self.assertIn("无法回答", answer)

    def test_score_session(self):
        case = next(iter(self.cases.values()))
        best = case["utility"]["best_option"]
        worst = min(
            case["utility"]["option_scores"],
            key=case["utility"]["option_scores"].get,
        )
        critical = next(
            fact for fact in case["oracle_facts"] if fact["criticality"] == "critical"
        )
        session = {
            "pre_decision": worst,
            "post_decision": best,
            "questions": [{"fact_id": critical["id"]}],
        }
        metrics = benchmark.score_session(case, session)
        self.assertGreater(metrics["decision_improvement"], 0)
        self.assertEqual(1.0, metrics["normalized_post_utility"])
        self.assertGreater(metrics["critical_fact_hit_rate"], 0)

    def test_schedule_is_reproducible_and_balanced(self):
        first = benchmark.build_schedule(self.cases, 123)
        second = benchmark.build_schedule(self.cases, 123)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 108)
        combinations = {
            (run["case_id"], run["condition"], run["model_seed"])
            for run in first
        }
        self.assertEqual(len(combinations), 108)
        for case_id in self.cases:
            for condition in benchmark.CONDITION_FILES:
                count = sum(
                    run["case_id"] == case_id
                    and run["condition"] == condition
                    for run in first
                )
                self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
