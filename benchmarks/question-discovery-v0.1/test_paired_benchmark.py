import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_evidence_catalog_is_exposed_only_to_catalog_conditions(self):
        case = self.pairs["product-pair-01"][0]
        native = json.dumps(
            paired_benchmark.preflight_messages(case, "N"), ensure_ascii=False
        )
        menu = json.dumps(
            paired_benchmark.preflight_messages(case, "Q"), ensure_ascii=False
        )
        self.assertNotIn("evidence_catalog", native)
        self.assertIn("evidence_catalog", menu)
        self.assertIn("E1", menu)
        self.assertNotIn("iPhone Safari", menu)
        self.assertNotIn("47%", menu)

    def test_each_catalog_query_maps_to_one_fact_in_each_hidden_branch(self):
        for variants in self.pairs.values():
            for case in variants:
                for evidence_id in ["E1", "E2", "E3", "E4"]:
                    fact_id, answer = benchmark.answer_evidence_query(
                        case, evidence_id, set()
                    )
                    self.assertIsNotNone(fact_id)
                    self.assertTrue(answer)

    def test_native_condition_contains_no_thinking_tool(self):
        messages = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "N"
        )
        prompt = messages[0]["content"]
        for term in ["QFT", "STORM", "钢人", "问题发现漏斗", "Who"]:
            self.assertNotIn(term, prompt)

    def test_minimal_discriminative_condition_is_registered_and_generic(self):
        self.assertEqual(
            "minimal-discriminative.md",
            paired_benchmark.PAIRED_CONDITION_FILES["D"],
        )
        messages = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "D"
        )
        prompt = messages[0]["content"]
        for required in ["行动敏感", "解释判别", "证据可答", "不要继续细化"]:
            self.assertIn(required, prompt)
        for case_specific in ["Safari", "单点登录", "路由策略", "低基线"]:
            self.assertNotIn(case_specific, prompt)

    def test_explicit_explanation_state_condition_is_registered_and_generic(self):
        self.assertEqual(
            "explicit-explanation-state.md",
            paired_benchmark.PAIRED_CONDITION_FILES["E"],
        )
        messages = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "E"
        )
        prompt = messages[0]["content"]
        for required in ["H1", "evidence_target", "TARGET", "不得复用"]:
            self.assertIn(required, prompt)
        for case_specific in ["Safari", "单点登录", "路由策略", "低基线"]:
            self.assertNotIn(case_specific, prompt)

    def test_evidence_menu_and_contract_conditions_are_registered(self):
        self.assertEqual(
            "evidence-menu.md", paired_benchmark.PAIRED_CONDITION_FILES["Q"]
        )
        self.assertEqual(
            "evidence-contract.md", paired_benchmark.PAIRED_CONDITION_FILES["F"]
        )
        menu_prompt = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "Q"
        )[0]["content"]
        contract_prompt = paired_benchmark.preflight_messages(
            self.pairs["product-pair-01"][0], "F"
        )[0]["content"]
        for required in ["EVIDENCE_ID", "目录", "不得自行提出目录外问题"]:
            self.assertIn(required, menu_prompt)
        for required in ["H1", "evidence_id", "TARGET", "三个不同"]:
            self.assertIn(required, contract_prompt)

    def test_catalog_plan_requires_unique_valid_evidence_ids(self):
        case = self.pairs["product-pair-01"][0]
        valid = (
            'EXPLANATIONS: [{"id":"H1","explanation":"机制一","evidence_id":"E1","action":"option_a"},'
            '{"id":"H2","explanation":"机制二","evidence_id":"E2","action":"option_b"},'
            '{"id":"H3","explanation":"机制三","evidence_id":"E3","action":"option_c"}]'
        )
        plan = benchmark.parse_catalog_explanation_plan(case, valid)
        self.assertEqual(["E1", "E2", "E3"], [item["evidence_id"] for item in plan])
        with self.assertRaisesRegex(ValueError, "三个不同"):
            benchmark.parse_catalog_explanation_plan(
                case, valid.replace('"evidence_id":"E3"', '"evidence_id":"E2"')
            )

    def test_catalog_conditions_execute_selected_atomic_evidence(self):
        case = self.pairs["product-pair-01"][0]
        config = {"url": "https://example.test/v1", "model_name": "test-model"}
        q_outputs = [
            'PRE_DECISION: option_b\nPRE_PROBABILITIES: {"option_a":0.35,"option_b":0.4,"option_c":0.15,"option_d":0.1}',
            "EVIDENCE_ID: E2",
            "DECIDE",
            'DECISION: option_a\nPROBABILITIES: {"option_a":0.8,"option_b":0.1,"option_c":0.05,"option_d":0.05}\nRATIONALE: 回放支持回滚引导。',
        ]
        with patch.object(
            benchmark, "api_chat_completion", side_effect=q_outputs
        ) as completion, patch.object(
            benchmark, "save_session", return_value=Path("q.json")
        ) as save:
            benchmark.run_api_session(
                case, "Q", config, prompt_file=benchmark.EVIDENCE_MENU_PROMPT
            )
        q_session = save.call_args.args[1]
        self.assertEqual(4, completion.call_count)
        self.assertEqual("E2", q_session["questions"][0]["evidence_id"])
        self.assertEqual("f2", q_session["questions"][0]["fact_id"])
        self.assertFalse(q_session["questions"][0]["oracle_match_disagreement"])
        self.assertEqual("evidence_catalog", q_session["oracle_mode"])
        q_metrics = benchmark.score_session(case, q_session)
        self.assertEqual(1.0, q_metrics["first_selection_critical"])
        self.assertEqual(0.0, q_metrics["distractor_evidence_selection_rate"])

        f_outputs = [
            'PRE_DECISION: option_b\nPRE_PROBABILITIES: {"option_a":0.35,"option_b":0.4,"option_c":0.15,"option_d":0.1}',
            'EXPLANATIONS: [{"id":"H1","explanation":"引导回归","evidence_id":"E2","action":"option_a"},{"id":"H2","explanation":"资格规则","evidence_id":"E3","action":"option_b"},{"id":"H3","explanation":"流量变化","evidence_id":"E4","action":"option_d"}]',
            "TARGET: H1",
            "DECIDE",
            'DECISION: option_a\nPROBABILITIES: {"option_a":0.8,"option_b":0.1,"option_c":0.05,"option_d":0.05}\nRATIONALE: 回放支持回滚引导。',
        ]
        with patch.object(
            benchmark, "api_chat_completion", side_effect=f_outputs
        ) as completion, patch.object(
            benchmark, "save_session", return_value=Path("f.json")
        ) as save:
            benchmark.run_api_session(
                case, "F", config, prompt_file=benchmark.EVIDENCE_CONTRACT_PROMPT
            )
        f_session = save.call_args.args[1]
        self.assertEqual(5, completion.call_count)
        self.assertEqual("H1", f_session["questions"][0]["explanation_target"])
        self.assertEqual("E2", f_session["questions"][0]["evidence_id"])
        self.assertEqual(["E2"], f_session["evidence_state"]["attempted_evidence_ids"])

    def test_catalog_conditions_support_direct_mode(self):
        case = self.pairs["product-pair-01"][0]
        q_inputs = [
            "option_b",
            '{"option_a":0.35,"option_b":0.4,"option_c":0.15,"option_d":0.1}',
            "E2",
            "DECIDE",
            "option_a",
            '{"option_a":0.8,"option_b":0.1,"option_c":0.05,"option_d":0.05}',
            "回放支持回滚引导。",
        ]
        with patch("builtins.input", side_effect=q_inputs), patch.object(
            benchmark, "save_session", return_value=Path("q-direct.json")
        ) as save:
            benchmark.run_direct_session(
                case, "Q", prompt_file=benchmark.EVIDENCE_MENU_PROMPT
            )
        self.assertEqual("E2", save.call_args.args[1]["questions"][0]["evidence_id"])

        plan = (
            '[{"id":"H1","explanation":"引导回归","evidence_id":"E2","action":"option_a"},'
            '{"id":"H2","explanation":"资格规则","evidence_id":"E3","action":"option_b"},'
            '{"id":"H3","explanation":"流量变化","evidence_id":"E4","action":"option_d"}]'
        )
        f_inputs = [
            "option_b",
            '{"option_a":0.35,"option_b":0.4,"option_c":0.15,"option_d":0.1}',
            plan,
            "H1",
            "DECIDE",
            "option_a",
            '{"option_a":0.8,"option_b":0.1,"option_c":0.05,"option_d":0.05}',
            "回放支持回滚引导。",
        ]
        with patch("builtins.input", side_effect=f_inputs), patch.object(
            benchmark, "save_session", return_value=Path("f-direct.json")
        ) as save:
            benchmark.run_direct_session(
                case, "F", prompt_file=benchmark.EVIDENCE_CONTRACT_PROMPT
            )
        session = save.call_args.args[1]
        self.assertEqual("H1", session["questions"][0]["explanation_target"])
        self.assertEqual("E2", session["questions"][0]["evidence_id"])

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
        self.assertEqual(32, len(runs))
        self.assertEqual(
            {"N", "A", "B", "C", "D", "E", "Q", "F"},
            {run["condition"] for run in runs},
        )

    def test_calibration_progress_resumes_completed_units(self):
        with tempfile.TemporaryDirectory() as directory:
            progress_path = Path(directory) / "progress.json"
            with patch.object(
                paired_benchmark,
                "run_pair",
                side_effect=[Path("one.json"), Path("two.json")],
            ) as first_run:
                paired_benchmark.run_calibration(
                    self.pairs,
                    ["N"],
                    {"product-pair-01"},
                    "api",
                    {"model_name": "test-model"},
                    1,
                    7,
                    progress_path,
                    2,
                )
            self.assertEqual(2, first_run.call_count)

            with patch.object(
                paired_benchmark, "run_pair", return_value=Path("three.json")
            ) as resumed:
                paired_benchmark.run_calibration(
                    self.pairs,
                    ["N"],
                    {"product-pair-01"},
                    "api",
                    {"model_name": "test-model"},
                    1,
                    7,
                    progress_path,
                    2,
                )
            self.assertEqual(1, resumed.call_count)
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(3, len(progress["completed"]))


if __name__ == "__main__":
    unittest.main()
