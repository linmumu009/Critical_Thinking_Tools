from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import audit_mode
import blind_mapping_review as bmr
import codex_direct_audit as cda
import compare_api_and_codex_audits as comparator


ROOT = Path(__file__).resolve().parent
PACKET_PATH = ROOT / "blind-review-v0.4" / "packet" / "blind-review-packet.json"
KEY_PATH = ROOT / "blind-review-v0.4" / "coordinator" / "unblinding-key.json"


class AuditModeIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packet = bmr.load_packet(PACKET_PATH)
        cls.key = bmr.load_json(KEY_PATH)
        cls.key_by_id = {
            item["review_id"]: item for item in cls.key["review_units"]
        }

    def review_for_unit(self, unit: dict) -> dict[str, dict[str, str]]:
        matches = self.key_by_id[unit["review_id"]]["auto_matches"]
        return {
            candidate["id"]: {
                "mapped_evidence_id": matches[candidate["id"]],
                "atomic_single_observation": "1",
                "fully_answerable_by_mapping": (
                    "0" if matches[candidate["id"]] == "NONE" else "1"
                ),
                "distinct_from_other_candidates": "1",
                "action_discriminating": "1",
            }
            for candidate in unit["candidates"]
        }

    def response_for_unit(self, unit: dict) -> dict:
        return {
            "packet_hash": self.packet["packet_hash"],
            "review_id": unit["review_id"],
            "processor_id": "codex-current-task",
            "direct_review": self.review_for_unit(unit),
        }

    def test_selector_keeps_api_and_codex_in_separate_processes(self) -> None:
        mode_1 = audit_mode.dispatch("1", "run", [])
        mode_2 = audit_mode.dispatch("2", "run", [])
        self.assertIn("automated_mapping_audit.py", mode_1[0][2])
        self.assertTrue(
            all("codex_direct_audit.py" in command[2] for command in mode_2)
        )
        self.assertNotEqual(mode_1[0][2], mode_2[0][2])

        source = (ROOT / "codex_direct_audit.py").read_text(encoding="utf-8")
        for forbidden in (
            "import benchmark",
            "api_chat_completion",
            "model-config",
            "automated-review-v0.4",
        ):
            self.assertNotIn(forbidden, source)

    def test_codex_task_is_blind_to_api_results_and_unblinding_key(self) -> None:
        progress = cda.initial_progress(self.packet)
        task = cda.task_for_next(self.packet, progress)
        self.assertIsNotNone(task)
        text = json.dumps(task, ensure_ascii=False)
        for forbidden in (
            "auto_matches",
            "generator",
            "model_seed",
            "oracle_facts",
            "unblinding",
            "correct_action",
        ):
            self.assertNotIn(forbidden, text)
        self.assertEqual(task["mode_id"], cda.MODE_ID)
        self.assertEqual(len(task["candidates"]), 8)
        self.assertEqual(len(task["evidence_catalog"]), 6)

    def test_direct_response_requires_exact_consistent_codex_schema(self) -> None:
        unit = self.packet["review_units"][0]
        payload = self.response_for_unit(unit)
        processor_id, parsed = cda.parse_direct_response(
            payload, self.packet, unit
        )
        self.assertEqual(processor_id, "codex-current-task")
        self.assertEqual(parsed, payload["direct_review"])

        inconsistent = json.loads(json.dumps(payload))
        candidate_id = next(iter(inconsistent["direct_review"]))
        inconsistent["direct_review"][candidate_id]["mapped_evidence_id"] = "NONE"
        inconsistent["direct_review"][candidate_id][
            "fully_answerable_by_mapping"
        ] = "1"
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            cda.parse_direct_response(inconsistent, self.packet, unit)

        human = json.loads(json.dumps(payload))
        human["processor_id"] = "human-reviewer"
        with self.assertRaisesRegex(ValueError, "not a human"):
            cda.parse_direct_response(human, self.packet, unit)

    def test_queue_locks_each_submission_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            output_dir = Path(raw_temp_dir)
            progress = cda.prepare(PACKET_PATH, output_dir)
            first_id = cda.next_review_id(progress)
            first_unit = next(
                unit
                for unit in self.packet["review_units"]
                if unit["review_id"] == first_id
            )
            response_path = output_dir / "response.json"
            response_path.write_text(
                json.dumps(self.response_for_unit(first_unit), ensure_ascii=False),
                encoding="utf-8",
            )
            updated = cda.submit_response(PACKET_PATH, output_dir, response_path)
            self.assertIn(first_id, updated["completed"])
            self.assertNotEqual(cda.next_review_id(updated), first_id)
            with self.assertRaisesRegex(ValueError, "current locked task"):
                cda.submit_response(PACKET_PATH, output_dir, response_path)

    def test_finalize_recomputes_full_codex_result_without_api_or_human(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            output_dir = Path(raw_temp_dir)
            progress = cda.initial_progress(self.packet)
            for unit in self.packet["review_units"]:
                progress["completed"][unit["review_id"]] = {
                    "processor_id": "codex-fixture",
                    "direct_review": self.review_for_unit(unit),
                    "response_filename": "fixture.json",
                }
            cda.save_json_atomic(output_dir / cda.PROGRESS_FILE, progress)
            result = cda.finalize(PACKET_PATH, KEY_PATH, output_dir)

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["mode_id"], cda.MODE_ID)
            self.assertEqual(result["codex_direct"]["external_api_call_count"], 0)
            self.assertEqual(result["codex_direct"]["human_review_count"], 0)
            self.assertEqual(
                result["original_auto_vs_codex_direct"][
                    "mapping_disagreement_count"
                ],
                0,
            )
            self.assertTrue((output_dir / "codex-direct-final.csv").exists())
            self.assertTrue((output_dir / "CODEX-DIRECT-REPORT.md").exists())

    def test_comparison_is_post_lock_and_read_only(self) -> None:
        fields = {
            "mapped_evidence_id": "E1",
            "atomic_single_observation": "1",
            "fully_answerable_by_mapping": "1",
            "distinct_from_other_candidates": "1",
            "action_discriminating": "1",
            "generator": "G0",
        }
        rows = {("R001", "C1"): fields}
        api_result = {
            "status": "complete",
            "packet_hash": "fixture-hash",
            "automated_consensus_gates": {"G0": {"coverage": False}},
            "decision": {"recommendation": "api-route"},
        }
        codex_result = {
            "status": "complete",
            "mode_id": cda.MODE_ID,
            "packet_hash": "fixture-hash",
            "codex_direct_gates": {"G0": {"coverage": False}},
            "decision": {"recommendation": "codex-route"},
        }
        result = comparator.compare(api_result, rows, codex_result, rows)
        self.assertEqual(result["comparison_type"], "post_lock_read_only")
        self.assertEqual(
            result["field_agreement"]["mapped_evidence_id"]["agreement_rate"],
            1.0,
        )
        self.assertEqual(result["gate_differences"], [])


if __name__ == "__main__":
    unittest.main()
