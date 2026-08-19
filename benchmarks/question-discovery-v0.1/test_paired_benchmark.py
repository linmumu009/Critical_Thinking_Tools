import json
import unittest

import benchmark
import paired_benchmark


class PairedBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = paired_benchmark.load_pairs()

    def test_counterfactual_pairs_validate(self):
        self.assertEqual([], paired_benchmark.validate_pairs(self.pairs))

    def test_twins_have_identical_public_payloads_and_different_best_actions(self):
        for variants in self.pairs.values():
            public_payloads = [benchmark.public_case(case) for case in variants]
            self.assertEqual(public_payloads[0], public_payloads[1])
            best = {benchmark.best_public_option(case) for case in variants}
            self.assertEqual(2, len(best))

    def test_hidden_facts_never_enter_preflight_messages(self):
        variants = self.pairs["product-pair-01"]
        messages = paired_benchmark.preflight_messages(variants[0], "A")
        rendered = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("oracle_facts", rendered)
        self.assertNotIn("Safari 脚本错误", rendered)
        self.assertEqual("product-pair-01", benchmark.public_case(variants[0])["case_id"])

    def test_native_condition_contains_no_thinking_tool(self):
        messages = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "N"
        )
        prompt = messages[0]["content"]
        for term in ["QFT", "STORM", "钢人", "问题发现漏斗", "Who"]:
            self.assertNotIn(term, prompt)

    def test_paired_score_rewards_correct_counterfactual_separation(self):
        variants = self.pairs["product-pair-01"]
        base_metrics = {
            "probability_quality_improvement": 0.2,
            "questions_used": 2.0,
            "no_fact_answer_rate": 0.0,
            "protocol_deviation_count": 0.0,
        }
        pre = {
            "option_a": 0.45,
            "option_b": 0.35,
            "option_c": 0.1,
            "option_d": 0.1,
        }
        sessions = [
            {
                "variant_id": "flow",
                "condition": "N",
                "pre_decision": "option_a",
                "pre_probabilities": pre,
                "post_decision": "option_a",
                "post_probabilities": {
                    "option_a": 0.8,
                    "option_b": 0.1,
                    "option_c": 0.05,
                    "option_d": 0.05,
                },
                "automatic_metrics": base_metrics,
            },
            {
                "variant_id": "eligibility",
                "condition": "N",
                "pre_decision": "option_a",
                "pre_probabilities": pre,
                "post_decision": "option_b",
                "post_probabilities": {
                    "option_a": 0.1,
                    "option_b": 0.8,
                    "option_c": 0.05,
                    "option_d": 0.05,
                },
                "automatic_metrics": base_metrics,
            },
        ]
        result = paired_benchmark.score_pair_sessions(variants, sessions)
        self.assertEqual(0.5, result["pre_choice_accuracy"])
        self.assertEqual(1.0, result["post_choice_accuracy"])
        self.assertAlmostEqual(0.0, result["pre_counterfactual_separation"])
        self.assertAlmostEqual(0.7, result["post_counterfactual_separation"])
        self.assertAlmostEqual(0.7, result["counterfactual_separation_gain"])

    def test_schedule_includes_native_and_all_other_conditions(self):
        runs = paired_benchmark.build_paired_schedule(self.pairs, seed=1, repeats=1)
        self.assertEqual(16, len(runs))
        self.assertEqual(
            {"N", "A", "B", "C"}, {run["condition"] for run in runs}
        )


if __name__ == "__main__":
    unittest.main()
