from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("research_question_session.py")
SPEC = importlib.util.spec_from_file_location("research_question_session", MODULE_PATH)
assert SPEC and SPEC.loader
rqs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rqs)


class ResearchQuestionSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = rqs.load_json(rqs.DEFAULT_PROFILE)
        self.pipeline = rqs.load_json(rqs.DEFAULT_PIPELINE)

    def complete_fixture(self, mode: str = "2") -> dict:
        session = rqs.initial_session(self.profile, self.pipeline, mode)
        session["status"] = "complete"
        session["input_manifest"] = [
            {
                "input_id": "I01",
                "kind": "spreadsheet",
                "location": "D:\\research\\questions.xlsx",
                "role": "local prior-work map and reality signal",
            }
        ]
        for stage in session["stage_trace"]:
            stage["status"] = "complete"
            stage["output_summary"] = f"completed {stage['stage_id']}"
            stage["artifact_refs"] = [f"session://{stage['stage_id']}"]
        session["evidence"] = [
            {
                "evidence_id": f"E{index:02d}",
                "source_title": f"Primary source {index}",
                "source_type": "paper",
                "source_location": f"https://example.org/paper-{index}",
                "published_date": "2026-01-01",
                "checked_date": "2026-08-21",
                "observation": f"Observed research tension {index}.",
                "interpretation": "This may change a post-training research decision.",
                "unknown": "The controlled mechanism remains unknown.",
            }
            for index in range(1, 9)
        ]
        gates = {field: True for field in rqs.HARD_GATES}
        scores = {
            field: {"value": 2, "reason": f"fixture reason for {field}"}
            for field in rqs.SCORE_DIMENSIONS
        }
        session["candidate_questions"] = [
            {
                "candidate_id": f"Q{index}",
                "short_title": f"Question {index}",
                "plain_question": f"Could effect {index} change the decision?",
                "why_it_matters": "Its answer determines whether the team funds the intervention.",
                "research_question": f"Research question {index}?",
                "question_identity": {
                    "unit_of_analysis": f"training example family {index}",
                    "comparison": "intervention versus matched control",
                    "outcome": "held-out capability",
                    "scope": "text-only verifiable tasks",
                },
                "secondary_questions": {
                    "mechanism": [f"What mechanism explains effect {index}?"],
                    "boundary": [f"When does effect {index} disappear?"],
                    "intervention": [],
                },
                "signal_ids": [f"E{index:02d}"],
                "closest_prior_ids": [f"E{((index) % 8) + 1:02d}"],
                "question_family": "mechanism",
                "cluster_id": f"C{index}",
                "decision_fork": {
                    "decision": "Choose which mechanism to test first.",
                    "answer_a": "The intervention changes the mechanism.",
                    "action_a": "Run the intervention experiment.",
                    "answer_b": "Compute or data explains the result.",
                    "action_b": "Do not invest in the intervention.",
                    "uncertain_action": "Collect one cheaper diagnostic.",
                    "reversal_condition": "A matched-control result reverses the choice.",
                },
                "evidence_path": "A matched small-model experiment can answer it.",
                "cheap_probe": {
                    "input": "Existing checkpoints and a small held-out set.",
                    "procedure": "Run a matched diagnostic before full training.",
                    "possible_outcomes": ["mechanism signal", "no controlled signal"],
                    "decision_rules": {
                        "keep": "Keep when the mechanism signal replicates.",
                        "narrow": "Narrow when it appears in one task only.",
                        "rewrite": "Rewrite when the measured proxy is invalid.",
                        "reject": "Reject when matched controls remove the effect.",
                    },
                },
                "probe_disposition": "keep",
                "hard_gates": copy.deepcopy(gates),
                "scores": copy.deepcopy(scores),
            }
            for index in range(1, 9)
        ]
        session["selection"] = {
            "outcome": "selected",
            "primary_candidate_id": "Q1",
            "backup_candidate_ids": ["Q2", "Q3"],
            "why_this_question": "It has the clearest decision fork and cheapest probe.",
            "incumbent_comparison": {
                "incumbent_question_refs": [],
                "outcome": "first-run",
                "reason": "No earlier selected question exists in this fixture.",
            },
            "research_question_contract": {
                "short_title": "Question 1",
                "plain_question": "Could effect 1 change the decision?",
                "why_it_matters": "Its answer determines whether the team funds the intervention.",
                "final_question": "Research question 1?",
                "question_identity": {
                    "unit_of_analysis": "training example family 1",
                    "comparison": "intervention versus matched control",
                    "outcome": "held-out capability",
                    "scope": "text-only verifiable tasks",
                },
                "secondary_questions": {
                    "mechanism": ["What mechanism explains effect 1?"],
                    "boundary": ["When does effect 1 disappear?"],
                    "intervention": [],
                },
                "triggering_signal_ids": ["E01"],
                "users_and_decision": "The research team must choose the next experiment.",
                "decision_deadline": "Before the next training allocation.",
                "key_concepts_and_boundaries": ["text-only models", "verifiable tasks"],
                "competing_answers": {
                    "a": "The intervention improves genuine generalization.",
                    "b": "The gain is verifier or compute overfitting.",
                    "unknown": "The available test is underpowered.",
                },
                "action_mapping": {
                    "if_a": "Invest in the full experiment.",
                    "if_b": "Stop this direction.",
                    "if_uncertain": "Collect the predefined diagnostic.",
                },
                "discriminating_evidence": "Matched-compute held-out behavior.",
                "reversal_result": "The effect disappears under matched controls.",
                "minimum_probe": "Compare two small checkpoints.",
                "cost_risk_ethics": "Low compute; public or licensed data only.",
                "stopping_condition": "Stop after the preregistered confidence bound.",
                "residual_unknowns": ["scaling behavior", "open-ended tasks"],
            },
        }
        session["decision_log"] = [
            {"candidate_id": "Q1", "decision": "keep", "reason": "passes the funnel"}
        ]
        return session

    def test_profile_defines_engine_swap_not_process_swap(self) -> None:
        rqs.validate_profile(self.profile, self.pipeline)
        self.assertEqual(
            self.profile["mode_definitions"]["1"]["engine"], "external_model_api"
        )
        self.assertEqual(
            self.profile["mode_definitions"]["2"]["engine"], "current_codex"
        )
        self.assertTrue(
            all(
                value is True
                for key, value in self.profile["process_invariants"].items()
                if key.startswith("same_")
            )
        )

    def test_both_modes_have_identical_pipeline_and_schema(self) -> None:
        api = rqs.initial_session(self.profile, self.pipeline, "1")
        codex = rqs.initial_session(self.profile, self.pipeline, "2")
        self.assertNotEqual(api["execution"], codex["execution"])
        for session in (api, codex):
            session.pop("execution")
        self.assertEqual(api, codex)

    def test_mode_2_adapter_forbids_external_model_api(self) -> None:
        session = rqs.initial_session(self.profile, self.pipeline, "2")
        self.assertEqual(session["execution"]["engine"], "current_codex")
        self.assertNotIn("api_config", session)
        prompt = (
            rqs.ROOT / session["execution"]["adapter_prompt"]
        ).read_text(encoding="utf-8")
        self.assertIn("不调用其配置的模型 API", prompt)

    def test_engine_prompts_embed_the_same_shared_stage_contract(self) -> None:
        api_prompt = rqs.build_stage_prompt(
            self.profile, self.pipeline, "1", "3_expand"
        )
        codex_prompt = rqs.build_stage_prompt(
            self.profile, self.pipeline, "2", "3_expand"
        )
        marker = "--- SHARED STAGE CONTRACT ---\n"
        self.assertNotEqual(api_prompt.split(marker)[0], codex_prompt.split(marker)[0])
        self.assertEqual(api_prompt.split(marker)[1], codex_prompt.split(marker)[1])
        self.assertIn('"question-formulation-technique"', api_prompt)
        self.assertIn('"storm-costorm"', api_prompt)
        cluster_prompt = rqs.build_stage_prompt(
            self.profile, self.pipeline, "2", "4_cluster"
        )
        self.assertIn("不得因为共享同一实验而合并不同未知", cluster_prompt)

    def test_complete_sessions_pass_for_both_engines(self) -> None:
        for mode in rqs.MODES:
            rqs.validate_session(
                self.complete_fixture(mode),
                self.profile,
                self.pipeline,
                require_complete=True,
            )

    def test_complete_status_enforces_stage_completion_without_cli_flag(self) -> None:
        session = self.complete_fixture()
        session["stage_trace"][7]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "pending stage"):
            rqs.validate_session(session, self.profile, self.pipeline)

    def test_stage_order_and_required_tools_cannot_drift(self) -> None:
        session = self.complete_fixture()
        session["stage_trace"][2], session["stage_trace"][3] = (
            session["stage_trace"][3],
            session["stage_trace"][2],
        )
        with self.assertRaisesRegex(ValueError, "order differs"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

        session = self.complete_fixture()
        session["stage_trace"][3]["tool_trace"].remove("storm-costorm")
        with self.assertRaisesRegex(ValueError, "tool sequence differs"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

    def test_selected_candidates_must_pass_scorecard_and_probe(self) -> None:
        session = self.complete_fixture()
        session["candidate_questions"][0]["hard_gates"]["answerable"] = False
        with self.assertRaisesRegex(ValueError, "every hard gate"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

        session = self.complete_fixture()
        session["candidate_questions"][1]["probe_disposition"] = "reject"
        with self.assertRaisesRegex(ValueError, "rejected probe"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

    def test_evidence_and_contract_references_are_traceable(self) -> None:
        session = self.complete_fixture()
        session["candidate_questions"][0]["signal_ids"] = ["MISSING"]
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

        session = self.complete_fixture()
        session["selection"]["research_question_contract"][
            "triggering_signal_ids"
        ] = ["MISSING"]
        with self.assertRaisesRegex(ValueError, "unknown signals"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

    def test_legacy_autonomous_sessions_are_not_official_mode_2_runs(self) -> None:
        legacy = json.loads(
            (
                rqs.ROOT
                / "results"
                / "2026-08-21-online-feedback-budget-allocation.json"
            ).read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ValueError, "legacy autonomous session"):
            rqs.validate_session(legacy, self.profile, self.pipeline, True)


    def test_semantic_audit_accepts_consistent_complete_session(self) -> None:
        result = rqs.semantic_audit(self.complete_fixture())
        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["metrics"]["selected_scores"]["Q1"], 12)

    def test_semantic_audit_rejects_contract_identity_drift(self) -> None:
        session = self.complete_fixture()
        session["selection"]["research_question_contract"]["question_identity"][
            "outcome"
        ] = "a different outcome"
        result = rqs.semantic_audit(session)
        self.assertFalse(result["passed"])
        self.assertIn(
            "contract_question_identity_mismatch",
            {item["code"] for item in result["errors"]},
        )

    def test_atomic_gate_rejects_compound_core_question(self) -> None:
        session = self.complete_fixture()
        session["candidate_questions"][0]["research_question"] = (
            "Does the intervention change capability; can normalization remove it?"
        )
        with self.assertRaisesRegex(ValueError, "compound research question"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

        session = self.complete_fixture()
        session["candidate_questions"][0]["research_question"] = (
            "干预位置是否会通过降低优化方差提高独立能力？"
        )
        with self.assertRaisesRegex(ValueError, "compound research question"):
            rqs.validate_session(session, self.profile, self.pipeline, True)

    def test_no_better_question_is_a_valid_complete_outcome(self) -> None:
        session = self.complete_fixture()
        session["selection"] = {
            "outcome": "no_better_question",
            "primary_candidate_id": None,
            "backup_candidate_ids": [],
            "why_this_question": "No new candidate beats the incumbent on clarity, value, and evidence.",
            "incumbent_comparison": {
                "incumbent_question_refs": ["results/previous-run.json#Q1"],
                "outcome": "no-better-question",
                "reason": "The incumbent remains clearer and has stronger reality evidence.",
            },
            "research_question_contract": None,
        }
        rqs.validate_session(session, self.profile, self.pipeline, True)
        audit = rqs.semantic_audit(session)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["metrics"]["selection_outcome"], "no_better_question")

    def test_historical_v2_session_remains_valid(self) -> None:
        historical = json.loads(
            (
                rqs.ROOT
                / "results"
                / "2026-08-27-shared-funnel-mode2-run-03.json"
            ).read_text(encoding="utf-8")
        )
        rqs.validate_session(historical, self.profile, self.pipeline, True)

    def test_semantic_audit_warns_when_primary_is_not_top_score(self) -> None:
        session = self.complete_fixture()
        for score in session["candidate_questions"][0]["scores"].values():
            score["value"] = 1
        result = rqs.semantic_audit(session)
        self.assertTrue(result["passed"])
        self.assertIn(
            "primary_not_top_score", {item["code"] for item in result["warnings"]}
        )

    def test_semantic_audit_rejects_non_iso_evidence_date(self) -> None:
        session = self.complete_fixture()
        session["evidence"][0]["checked_date"] = "today"
if __name__ == "__main__":
    unittest.main()
