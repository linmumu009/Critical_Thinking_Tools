from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import benchmark
import candidate_benchmark as cb


def valid_candidates() -> list[dict[str, str]]:
    return [
        {"id": f"C{index}", "question": f"第 {index} 个原子证据问题是什么？"}
        for index in range(1, 9)
    ]


class CandidateBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pairs = cb.load_pairs()

    def test_new_pairs_validate(self) -> None:
        self.assertEqual(cb.validate_pairs(self.pairs), [])

    def test_public_payload_is_branch_symmetric_and_hides_matcher_catalog(self) -> None:
        for variants in self.pairs.values():
            payloads = [cb.public_generator_case(case) for case in variants]
            self.assertEqual(payloads[0], payloads[1])
            self.assertIn("evidence_capabilities", payloads[0])
            self.assertNotIn("evidence_catalog", payloads[0])
            native_payload = benchmark.public_case(variants[0])
            self.assertEqual(
                native_payload["evidence_capabilities"], payloads[0]["evidence_capabilities"]
            )

    def test_best_option_positions_are_balanced(self) -> None:
        positions = [
            benchmark.best_public_option(case)
            for variants in self.pairs.values()
            for case in variants
        ]
        self.assertEqual(
            {option: positions.count(option) for option in sorted(set(positions))},
            {"option_a": 2, "option_b": 2, "option_c": 2, "option_d": 2},
        )

    def test_parse_candidates_requires_ordered_c1_to_c8(self) -> None:
        payload = valid_candidates()
        raw = "CANDIDATES: " + json.dumps(payload, ensure_ascii=False)
        self.assertEqual(cb.parse_candidates(raw), payload)
        payload[0]["id"] = "C8"
        with self.assertRaisesRegex(ValueError, "C1-C8"):
            cb.parse_candidates(
                "CANDIDATES: " + json.dumps(payload, ensure_ascii=False)
            )

    def test_parse_matches_requires_all_candidates_and_catalog_ids(self) -> None:
        candidates = valid_candidates()
        matches = {f"C{index}": "E1" if index < 5 else "NONE" for index in range(1, 9)}
        raw = "MATCHES: " + json.dumps(matches)
        self.assertEqual(cb.parse_matches(raw, candidates, {"E1", "E2"}), matches)
        matches["C8"] = "E9"
        with self.assertRaisesRegex(ValueError, "values"):
            cb.parse_matches("MATCHES: " + json.dumps(matches), candidates, {"E1"})

    def test_normalize_menu_removes_none_and_duplicate_matches(self) -> None:
        candidates = valid_candidates()
        matches = {
            "C1": "E1",
            "C2": "E1",
            "C3": "NONE",
            "C4": "E2",
            "C5": "NONE",
            "C6": "E3",
            "C7": "E2",
            "C8": "NONE",
        }
        menu = cb.normalize_menu(candidates, matches)
        self.assertEqual([item["id"] for item in menu], ["C1", "C4", "C6"])
        self.assertEqual(
            [item["base_evidence_id"] for item in menu], ["E1", "E2", "E3"]
        )

    def test_derived_cases_rebind_candidate_ids_without_changing_answers(self) -> None:
        variants = self.pairs["product-pair-02"]
        menu = [
            {"id": "C2", "question": "排序回放结果如何？", "base_evidence_id": "E2"},
            {"id": "C5", "question": "流量构成变化吗？", "base_evidence_id": "E5"},
        ]
        derived = cb.derive_cases(variants, menu)
        self.assertEqual(derived[0]["question_budget"], 2)
        self.assertEqual(
            [item["id"] for item in derived[0]["evidence_catalog"]], ["C2", "C5"]
        )
        self.assertEqual(
            [fact["evidence_id"] for fact in derived[0]["oracle_facts"]],
            ["C2", "C5"],
        )
        self.assertEqual(
            derived[0]["oracle_facts"][0]["source_evidence_id"], "E2"
        )

    def test_derived_case_without_critical_facts_scores_zero_hit_rate(self) -> None:
        variants = self.pairs["product-pair-02"]
        supporting = next(
            fact
            for fact in variants[0]["oracle_facts"]
            if fact["criticality"] != "critical"
        )
        menu = [
            {
                "id": "C1",
                "question": "这项辅助证据显示什么？",
                "base_evidence_id": supporting["evidence_id"],
            }
        ]
        case = cb.derive_cases(variants, menu)[0]
        best = benchmark.best_public_option(case)
        session = {
            "pre_decision": best,
            "post_decision": best,
            "questions": [],
        }
        metrics = benchmark.score_session(case, session)
        self.assertEqual(metrics["critical_fact_hit_rate"], 0.0)

    def test_candidate_metrics_report_branch_critical_coverage(self) -> None:
        variants = self.pairs["product-pair-02"]
        candidates = valid_candidates()
        matches = {
            "C1": "E1",
            "C2": "E2",
            "C3": "E3",
            "C4": "NONE",
            "C5": "E1",
            "C6": "NONE",
            "C7": "NONE",
            "C8": "NONE",
        }
        menu = cb.normalize_menu(candidates, matches)
        metrics = cb.candidate_metrics(variants, candidates, matches, menu)
        self.assertEqual(metrics["duplicate_match_count"], 1)
        self.assertEqual(metrics["branch_critical_coverage"], [1.0, 1.0])
        self.assertEqual(metrics["both_branches_full_critical_coverage"], 1.0)

    def test_full_funnel_generator_is_registered_with_shared_tools(self) -> None:
        self.assertEqual(cb.GENERATOR_FILES["GF"], "generator-full-funnel.md")
        prompt = (benchmark.PROMPTS_DIR / cb.GENERATOR_FILES["GF"]).read_text(
            encoding="utf-8"
        )
        for required in (
            "5W1H", "苏格拉底", "QFT", "STORM", "双向钢人", "竞争假设", "硬门槛"
        ):
            self.assertIn(required, prompt)


    def test_schedule_contains_native_and_five_generators(self) -> None:
        schedule = cb.build_registered_schedule(self.pairs, 20260901, repeats=3)
        self.assertEqual(len(schedule), 84)
        counts = {condition: 0 for condition in cb.RUN_CONDITIONS}
        for run in schedule:
            counts[run["condition"]] += 1
        self.assertEqual(counts, {condition: 12 for condition in cb.RUN_CONDITIONS})
        self.assertEqual(
            sorted(set(run["randomization_seed"] for run in schedule)),
            [20260901, 20260902, 20260903],
        )

    @patch("candidate_benchmark.benchmark.api_chat_completion")
    def test_api_generation_and_blind_matching_use_two_calls(self, mock_call) -> None:
        candidates = valid_candidates()
        matches = {f"C{index}": f"E{(index - 1) % 6 + 1}" for index in range(1, 9)}
        mock_call.side_effect = [
            "CANDIDATES: " + json.dumps(candidates, ensure_ascii=False),
            "MATCHES: " + json.dumps(matches),
        ]
        case = self.pairs["product-pair-02"][0]
        parsed, parsed_matches, metadata = cb.generate_and_match(
            case,
            "G0",
            "api",
            {"url": "https://example.invalid/v1", "model_name": "test"},
            1,
        )
        self.assertEqual(parsed, candidates)
        self.assertEqual(parsed_matches, matches)
        self.assertEqual(metadata["generator_model_call_count"], 1)
        self.assertEqual(metadata["matcher_model_call_count"], 1)
        self.assertEqual(mock_call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
