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


if __name__ == "__main__":
    unittest.main()
