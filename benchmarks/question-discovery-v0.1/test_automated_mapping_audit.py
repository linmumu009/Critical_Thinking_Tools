from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import automated_mapping_audit as ama
import blind_mapping_review as bmr


ROOT = Path(__file__).resolve().parent
PACKET_PATH = ROOT / "blind-review-v0.4" / "packet" / "blind-review-packet.json"
KEY_PATH = ROOT / "blind-review-v0.4" / "coordinator" / "unblinding-key.json"


class AutomatedMappingAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packet = bmr.load_packet(PACKET_PATH)
        cls.key = bmr.load_json(KEY_PATH)
        cls.unit = cls.packet["review_units"][0]
        cls.key_by_id = {
            item["review_id"]: item for item in cls.key["review_units"]
        }

    def audit_for_unit(self, unit: dict) -> dict[str, dict[str, str]]:
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

    def test_parse_audit_accepts_exact_schema_and_rejects_inconsistency(self) -> None:
        audit = self.audit_for_unit(self.unit)
        raw = "analysis omitted\nAUDIT: " + json.dumps(audit, ensure_ascii=False)
        self.assertEqual(ama.parse_audit(raw, self.unit), audit)
        first = next(iter(audit.values()))
        first["mapped_evidence_id"] = "NONE"
        first["fully_answerable_by_mapping"] = "1"
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            ama.parse_audit("AUDIT: " + json.dumps(audit), self.unit)

    def test_arbitration_must_cover_only_disagreements(self) -> None:
        judge_a = self.audit_for_unit(self.unit)
        judge_b = json.loads(json.dumps(judge_a))
        candidate_id = next(
            key for key, row in judge_b.items() if row["mapped_evidence_id"] != "NONE"
        )
        original = judge_b[candidate_id]["mapped_evidence_id"]
        judge_b[candidate_id]["mapped_evidence_id"] = "E2" if original != "E2" else "E1"
        disagreements = ama.disagreement_items(judge_a, judge_b)
        self.assertEqual(len(disagreements), 1)
        decisions = [
            {
                "candidate_id": candidate_id,
                "field": "mapped_evidence_id",
                "final_value": original,
            }
        ]
        parsed = ama.parse_decisions(
            "DECISIONS: " + json.dumps(decisions), self.unit, disagreements
        )
        self.assertEqual(parsed, decisions)
        with self.assertRaisesRegex(ValueError, "every disagreement"):
            ama.parse_decisions("DECISIONS: []", self.unit, disagreements)

    def test_role_messages_and_safe_config_do_not_leak_results_or_key(self) -> None:
        text = json.dumps(ama.judge_messages(self.unit, "judge_a"), ensure_ascii=False)
        for forbidden in (
            "auto_matches",
            "generator",
            "model_seed",
            "oracle_facts",
            "unblinding",
        ):
            self.assertNotIn(forbidden, text)
        base = {
            "url": "https://main.invalid/v1",
            "api_key": "secret-main",
            "model_name": "main-model",
            "max_tokens": 1536,
            "thinking_budget": 512,
            "audit_judge_b_url": "https://judge.invalid/v1",
            "audit_judge_b_api_key": "secret-judge",
            "audit_judge_b_model_name": "judge-model",
        }
        config = ama.role_config(base, "judge_b")
        self.assertEqual(config["model_name"], "judge-model")
        safe = ama.safe_role_config(config)
        self.assertNotIn("api_key", safe)
        self.assertNotIn("url", safe)
        self.assertEqual(safe["temperature"], 0)

    def test_finalize_full_automatic_consensus_without_api(self) -> None:
        configs = {
            role: {
                "model_name": "fixture-model",
                "temperature": 0,
                "max_tokens": 1536,
                "thinking_budget": 512,
            }
            for role in ("judge_a", "judge_b", "arbitrator")
        }
        progress = ama.initial_progress(self.packet, configs)
        for unit in self.packet["review_units"]:
            parsed = self.audit_for_unit(unit)
            progress["completed"][unit["review_id"]] = {
                "judge_a": {
                    "parsed": parsed,
                    "raw": "fixture",
                    "model_call_count": 1,
                    "protocol_deviations": [],
                },
                "judge_b": {
                    "parsed": parsed,
                    "raw": "fixture",
                    "model_call_count": 1,
                    "protocol_deviations": [],
                },
                "arbitrator": {
                    "status": "not_needed",
                    "decisions": [],
                    "model_call_count": 0,
                    "protocol_deviations": [],
                },
            }
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            output_dir = Path(raw_temp_dir)
            result = ama.finalize_automated_audit(
                self.packet, self.key, progress, output_dir
            )
            self.assertEqual(
                result["decision"]["recommendation"],
                "proceed_to_gq2_generator_development",
            )
            self.assertEqual(
                result["original_auto_vs_automated_consensus"][
                    "mapping_disagreement_count"
                ],
                0,
            )
            self.assertNotIn("auto_vs_human_consensus", result)
            self.assertIn(
                "automated_consensus", result["condition_comparison"]["G0"]
            )
            self.assertTrue(result["automated_audit"]["same_model_for_all_roles"])
            self.assertEqual(
                result["automated_audit"]["model_call_count"],
                {"judge_a": 48, "judge_b": 48, "arbitrator": 0},
            )
            self.assertEqual(
                result["automated_audit"]["format_repair_count"],
                {"judge_a": 0, "judge_b": 0, "arbitrator": 0},
            )
            self.assertEqual(
                result["automated_audit"]["primary_judgment_call_count"],
                {"judge_a": 48, "judge_b": 48, "arbitrator": 0},
            )
            self.assertEqual(result["automated_audit"]["format_repair_rate"], 0)
            self.assertTrue((output_dir / "AUTOMATED-SENSITIVITY-REPORT.md").exists())
            self.assertTrue((output_dir / "consensus-mappings.csv").exists())


if __name__ == "__main__":
    unittest.main()
