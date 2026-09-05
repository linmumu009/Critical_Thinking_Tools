import copy
import unittest

from completion_order_bias_probe import analyze, make_demo, validate_payload


class CompletionOrderBiasProbeTests(unittest.TestCase):
    def test_demo_is_explicitly_synthetic_and_meets_contract(self) -> None:
        result = analyze(make_demo())
        self.assertEqual(result["scientific_status"], "synthetic_pipeline_check_only")
        self.assertTrue(result["fixed_set_audit"]["passed"])
        self.assertEqual(result["fixed_set_audit"]["seed_count"], 3)
        self.assertEqual(result["fixed_set_audit"]["injection_level_count"], 3)
        self.assertEqual(result["preregistered_decision"]["criterion_outcome"], "stop_or_narrow")
        self.assertEqual(result["preregistered_decision"]["outcome"], "pipeline_check_only")

    def test_real_replay_enforces_preregistered_trajectory_count(self) -> None:
        payload = make_demo()
        payload["scientific_status"] = "real_replay"
        with self.assertRaisesRegex(ValueError, "256-512 trajectories"):
            validate_payload(payload)

    def test_rejects_invalid_scientific_status(self) -> None:
        payload = make_demo()
        payload["scientific_status"] = "unknown"
        with self.assertRaisesRegex(ValueError, "scientific_status"):
            validate_payload(payload)

    def test_rejects_missing_counterfactual_cell(self) -> None:
        payload = make_demo()
        payload["runs"].pop()
        with self.assertRaisesRegex(ValueError, "missing run cell"):
            validate_payload(payload)

    def test_rejects_changed_trajectory_set(self) -> None:
        payload = make_demo()
        payload["runs"][0]["updates"][0]["weights"] = payload["runs"][0]["updates"][0]["weights"][:-1]
        payload["runs"][0]["updates"][1]["weights"] = [
            item for item in payload["runs"][0]["updates"][1]["weights"] if item["trajectory_id"] != "t11"
        ]
        payload["runs"][0]["updates"][2]["weights"] = [
            item for item in payload["runs"][0]["updates"][2]["weights"] if item["trajectory_id"] != "t11"
        ]
        payload["runs"][0]["updates"][3]["weights"] = [
            item for item in payload["runs"][0]["updates"][3]["weights"] if item["trajectory_id"] != "t11"
        ]
        with self.assertRaisesRegex(ValueError, "fixed trajectory set"):
            validate_payload(payload)

    def test_threshold_controls_outcome_without_changing_measurements(self) -> None:
        payload = make_demo()
        baseline = analyze(payload)
        relaxed = copy.deepcopy(payload)
        relaxed["thresholds"]["min_mean_cosine_gap"] = 0.0
        relaxed["thresholds"]["min_dose_spearman"] = -1.0
        result = analyze(relaxed)
        self.assertEqual(
            baseline["preregistered_decision"]["mean_cosine_gap_random_minus_real"],
            result["preregistered_decision"]["mean_cosine_gap_random_minus_real"],
        )
        self.assertEqual(result["preregistered_decision"]["criterion_outcome"], "continue")
        self.assertEqual(result["preregistered_decision"]["outcome"], "pipeline_check_only")


if __name__ == "__main__":
    unittest.main()
