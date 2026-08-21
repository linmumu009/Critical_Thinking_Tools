from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "research_question_session", ROOT / "research_question_session.py"
)
assert SPEC is not None and SPEC.loader is not None
rqs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rqs)


class ResearchQuestionSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = rqs.load_json(ROOT / "research-profile.json")

    def complete_fixture(self) -> dict:
        session = rqs.initial_session(copy.deepcopy(self.profile))
        session["status"] = "complete"
        session["evidence"] = [
            {
                "evidence_id": f"E{index:03d}",
                "source_url": f"https://example.org/source-{index}",
                "source_title": f"Primary source {index}",
                "source_type": "paper",
                "published_date": "2026-01-01",
                "checked_date": "2026-08-21",
                "observation": f"Observed research tension {index}",
                "relevance": "It leaves a falsifiable post-training uncertainty.",
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
                "research_question": f"Research question {index}?",
                "observation_ids": [f"E{index:03d}"],
                "closest_prior_ids": [f"E{index + 1:03d}"],
                "hypothesis": "A controlled intervention changes the measured outcome.",
                "counter_hypothesis": "The apparent change is explained by compute or data.",
                "falsification_rule": "Reject the hypothesis when the controlled effect vanishes.",
                "minimum_experiment": "Run a small-model controlled comparison.",
                "primary_risk": "The evaluator may reward its own artifacts.",
                "hard_gates": copy.deepcopy(gates),
                "scores": copy.deepcopy(scores),
            }
            for index in range(1, 7)
        ]
        session["selection"] = {
            "primary_candidate_id": "Q1",
            "backup_candidate_ids": ["Q2", "Q3"],
            "why_this_question": "It has the clearest gap and cheapest decisive test.",
            "research_question_contract": {
                "final_question": "Does the intervention improve out-of-domain behavior?",
                "hypothesis": "It improves behavior beyond the training verifier distribution.",
                "counter_hypothesis": "It only overfits the verifier distribution.",
                "independent_variables": ["training intervention"],
                "dependent_variables": ["held-out task accuracy"],
                "controls": ["tokens", "compute", "base checkpoint"],
                "minimum_experiment": "Compare two small checkpoints with matched compute.",
                "falsification_rule": "Reject if held-out gains disappear under matched compute.",
                "expected_contribution": "Separate real generalization from verifier overfitting.",
                "boundary_conditions": ["text-only models", "verifiable tasks"],
                "compute_budget_assumption": "Two small-model post-training runs.",
                "data_requirements": ["training prompts", "held-out verifier family"],
            },
        }
        session["decision_log"] = [
            {"candidate_id": "Q1", "decision": "keep", "reason": "passes gates"}
        ]
        return session

    def test_profile_fixes_single_business_and_codex_processor(self) -> None:
        rqs.validate_profile(self.profile)
        self.assertIn("研究问题", self.profile["business_goal"])
        constraints = self.profile["execution_constraints"]
        self.assertEqual(constraints["processor"], "current_codex")
        self.assertFalse(constraints["external_model_api_allowed"])
        self.assertFalse(constraints["human_scoring_required"])

    def test_initial_session_contains_no_api_or_review_queue(self) -> None:
        session = rqs.initial_session(self.profile)
        self.assertEqual(session["mode_id"], rqs.MODE_ID)
        self.assertEqual(session["status"], "collecting_evidence")
        self.assertNotIn("api_config", session)
        self.assertNotIn("review_order", session)
        rqs.validate_session(session)

    def test_incomplete_session_cannot_be_claimed_complete(self) -> None:
        session = rqs.initial_session(self.profile)
        with self.assertRaisesRegex(ValueError, "not complete"):
            rqs.validate_session(session, require_complete=True)

    def test_complete_research_question_contract_passes(self) -> None:
        session = self.complete_fixture()
        rqs.validate_session(session, require_complete=True)

    def test_evidence_and_prior_references_must_be_traceable(self) -> None:
        session = self.complete_fixture()
        session["evidence"][0]["source_url"] = "not-a-url"
        with self.assertRaisesRegex(ValueError, "HTTP"):
            rqs.validate_session(session, require_complete=True)

        session = self.complete_fixture()
        session["candidate_questions"][0]["closest_prior_ids"] = ["MISSING"]
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            rqs.validate_session(session, require_complete=True)

    def test_primary_must_pass_gates_and_backups_must_be_distinct(self) -> None:
        session = self.complete_fixture()
        session["candidate_questions"][0]["hard_gates"]["falsifiable"] = False
        with self.assertRaisesRegex(ValueError, "every hard gate"):
            rqs.validate_session(session, require_complete=True)

        session = self.complete_fixture()
        session["selection"]["backup_candidate_ids"] = ["Q2", "Q2"]
        with self.assertRaisesRegex(ValueError, "distinct valid backup"):
            rqs.validate_session(session, require_complete=True)


if __name__ == "__main__":
    unittest.main()
