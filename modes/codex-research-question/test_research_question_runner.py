from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import research_question_runner as runner


class ResearchQuestionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name) / "run"
        self.state = runner.initialize_run("test-run", "2", self.run_dir, [])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def envelope(
        self,
        stage_id: str,
        updates: dict | None = None,
    ) -> dict:
        return {
            "schema_version": "1.0",
            "run_id": "test-run",
            "stage_id": stage_id,
            "output_summary": f"completed {stage_id}",
            "artifact_refs": [f"stage://{stage_id}"],
            "payload": {"notes": "test payload"},
            "session_updates": updates or {},
        }

    def save_envelope(self, value: dict) -> Path:
        path = Path(self.temporary.name) / "envelope.json"
        runner.save_json(path, value)
        return path

    def test_init_and_next_packet_preserve_mode_2_api_isolation(self) -> None:
        packet_path = runner.build_packet(self.run_dir)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["stage_id"], "0_goal")
        self.assertEqual(packet["engine"], "current_codex")
        self.assertNotIn("api_key", json.dumps(packet))
        self.assertEqual(
            packet["output_contract"]["allowed_session_update_fields"],
            ["decision_log", "input_manifest"],
        )

    def test_checkpoint_advances_and_resume_uses_next_incomplete_stage(self) -> None:
        envelope = self.envelope(
            "0_goal",
            {
                "input_manifest": [
                    {
                        "input_id": "I01",
                        "kind": "user_constraint",
                        "location": "D:\\research\\brief.md",
                        "role": "research scope",
                    }
                ],
                "decision_log": ["goal frozen"],
            },
        )
        runner.checkpoint_stage(self.run_dir, self.save_envelope(envelope))
        packet = json.loads(runner.build_packet(self.run_dir).read_text(encoding="utf-8"))
        self.assertEqual(packet["stage_id"], "1_reality_signals")
        state, session, _ = runner.load_run(self.run_dir)
        self.assertEqual(state["current_stage"], "1_reality_signals")
        self.assertEqual(session["stage_trace"][0]["status"], "complete")
        self.assertEqual(len(state["checkpoints"]), 1)

    def test_checkpoint_rejects_stage_skipping(self) -> None:
        with self.assertRaisesRegex(ValueError, "another run or stage"):
            runner.checkpoint_stage(
                self.run_dir,
                self.save_envelope(self.envelope("3_expand")),
            )

    def test_failure_record_does_not_advance_stage(self) -> None:
        runner.record_failure(self.run_dir, "temporary search failure")
        state, session, _ = runner.load_run(self.run_dir)
        self.assertEqual(len(state["failures"]), 1)
        self.assertEqual(runner.rqs.next_stage_id(session), "0_goal")

    def test_load_rejects_cross_run_ledger(self) -> None:
        _, _, ledger = runner.load_run(self.run_dir)
        ledger["run_id"] = "another-run"
        runner.save_json(runner.run_paths(self.run_dir)["ledger"], ledger)
        with self.assertRaisesRegex(ValueError, "identities differ"):
            runner.load_run(self.run_dir)


    def test_ledger_rejects_duplicate_query_ids(self) -> None:
        item = {
            "query_id": "Q01",
            "text": "test query",
            "provider": "web",
            "scope": "primary sources",
            "executed_at_utc": runner.utc_now(),
            "result_count": 2,
        }
        runner.update_ledger(self.run_dir, "query", item)
        with self.assertRaisesRegex(ValueError, "duplicate query_id"):
            runner.update_ledger(self.run_dir, "query", item)

    def test_ledger_audit_requires_online_sources_and_selected_collisions(self) -> None:
        session = {
            "evidence": [
                {
                    "evidence_id": "E01",
                    "source_location": "https://example.org/paper",
                }
            ],
            "selection": {
                "primary_candidate_id": "C01",
                "backup_candidate_ids": ["C02", "C03"],
            },
        }
        incomplete = {
            "queries": [],
            "source_decisions": [],
            "collision_reviews": [],
        }
        result = runner.ledger_audit(session, incomplete)
        self.assertFalse(result["passed"])
        self.assertIn("missing_query_log", {item["code"] for item in result["errors"]})

        complete = {
            "queries": [{"query_id": "Q01"}],
            "source_decisions": [
                {
                    "evidence_id": "E01",
                    "query_ids": ["Q01"],
                    "source_location": "https://example.org/paper",
                    "disposition": "include",
                }
            ],
            "collision_reviews": [
                {
                    "candidate_id": candidate_id,
                    "nonredundant_increment": "a distinct controlled mechanism",
                    "query_ids": ["Q01"],
                    "closest_evidence_ids": ["E01"],
                    "disposition": "keep",
                }
                for candidate_id in ("C01", "C02", "C03")
            ],
        }
        self.assertTrue(runner.ledger_audit(session, complete)["passed"])


if __name__ == "__main__":
    unittest.main()
