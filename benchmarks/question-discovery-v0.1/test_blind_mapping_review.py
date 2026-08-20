from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import blind_mapping_review as bmr


ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = ROOT / "blind-review-v0.4"
PACKET_PATH = PACKAGE_DIR / "packet" / "blind-review-packet.json"
KEY_PATH = PACKAGE_DIR / "coordinator" / "unblinding-key.json"


class BlindMappingReviewTests(unittest.TestCase):
    def test_formal_ledgers_resolve_exact_registered_artifacts(self) -> None:
        records, completed_at = bmr.load_candidate_records()
        self.assertEqual(len(records), 48)
        self.assertEqual(
            len(
                {
                    (record["pair_id"], record["generator"], record["model_seed"])
                    for record in records
                }
            ),
            48,
        )
        self.assertTrue(completed_at.endswith("+00:00"))
        self.assertTrue(all(not Path(record["source_file"]).is_absolute() for record in records))

    def test_committed_packet_is_deterministic_complete_and_blind(self) -> None:
        packet = bmr.load_packet(PACKET_PATH)
        key = bmr.load_json(KEY_PATH)
        self.assertEqual(packet["review_unit_count"], 48)
        self.assertEqual(packet["candidate_row_count"], 384)
        self.assertEqual(len(packet["review_units"]), 48)
        self.assertEqual(len(key["review_units"]), 48)
        self.assertEqual(key["packet_hash"], packet["packet_hash"])
        serialized = bmr.canonical_json(packet)
        for forbidden in (
            '"generator"',
            '"model_seed"',
            '"auto_matches"',
            '"candidate_metrics"',
            '"oracle_facts"',
        ):
            self.assertNotIn(forbidden, serialized)

        records, completed_at = bmr.load_candidate_records()
        regenerated, _ = bmr.packet_without_hash(
            records, completed_at, bmr.RANDOMIZATION_SEED
        )
        regenerated = bmr.add_packet_hash(regenerated)
        self.assertEqual(packet, regenerated)

        for reviewer_id in ("reviewer-1", "reviewer-2"):
            path = PACKAGE_DIR / "forms" / f"{reviewer_id}.csv"
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 384)
            self.assertTrue(all(row["packet_hash"] == packet["packet_hash"] for row in rows))
            self.assertTrue(all(row["reviewer_id"] == reviewer_id for row in rows))
            self.assertTrue(all(not row["mapped_evidence_id"] for row in rows))

        review_form_path = PACKAGE_DIR / "packet" / "review-form.html"
        review_form = review_form_path.read_text(encoding="utf-8")
        self.assertEqual(review_form, bmr.review_form_html(packet))
        self.assertIn(packet["packet_hash"], review_form)
        self.assertNotIn("unblinding-key", review_form)
        self.assertNotIn("auto_matches", review_form)
        self.assertNotIn("https://", review_form)
        self.assertNotIn("http://", review_form)

        expected_bundle_entries = {
            "README.md",
            "review-form.html",
            "blind-review-packet.md",
            "blind-review-packet.json",
            "reviewer-template.csv",
        }
        for reviewer_id in ("reviewer-1", "reviewer-2"):
            bundle_path = PACKAGE_DIR / "reviewer-bundles" / f"{reviewer_id}.zip"
            with zipfile.ZipFile(bundle_path) as archive:
                self.assertEqual(set(archive.namelist()), expected_bundle_entries)
                bundle_text = "\n".join(
                    archive.read(name).decode("utf-8-sig")
                    for name in archive.namelist()
                )
            self.assertIn(packet["packet_hash"], bundle_text)
            self.assertNotIn("unblinding-key", bundle_text)
            self.assertNotIn("auto_matches", bundle_text)

    def _completed_rows(
        self, packet: dict, key: dict, reviewer_id: str
    ) -> list[dict[str, str]]:
        key_by_id = {item["review_id"]: item for item in key["review_units"]}
        rows = bmr.review_template_rows(packet, reviewer_id)
        for row in rows:
            mapping = key_by_id[row["review_id"]]["auto_matches"][row["candidate_id"]]
            row.update(
                {
                    "mapped_evidence_id": mapping,
                    "atomic_single_observation": "1",
                    "fully_answerable_by_mapping": "0" if mapping == "NONE" else "1",
                    "distinct_from_other_candidates": "1",
                    "action_discriminating": "1",
                }
            )
        return rows

    def test_two_reviewers_adjudication_and_sensitivity_round_trip(self) -> None:
        packet = bmr.load_packet(PACKET_PATH)
        key = bmr.load_json(KEY_PATH)
        reviewer_one_rows = self._completed_rows(packet, key, "human-a")
        reviewer_two_rows = self._completed_rows(packet, key, "human-b")
        changed_row = next(
            row for row in reviewer_two_rows if row["mapped_evidence_id"] != "NONE"
        )
        original_mapping = changed_row["mapped_evidence_id"]
        changed_row["mapped_evidence_id"] = "E2" if original_mapping != "E2" else "E1"

        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            reviewer_one_path = temp_dir / "reviewer-one.csv"
            reviewer_two_path = temp_dir / "reviewer-two.csv"
            adjudication_path = temp_dir / "adjudication.csv"
            bmr.write_csv(reviewer_one_path, bmr.REVIEW_COLUMNS, reviewer_one_rows)
            bmr.write_csv(reviewer_two_path, bmr.REVIEW_COLUMNS, reviewer_two_rows)

            prepared = bmr.prepare_adjudication(
                PACKET_PATH, reviewer_one_path, reviewer_two_path, adjudication_path
            )
            self.assertEqual(prepared["disagreements"], 1)
            with adjudication_path.open("r", encoding="utf-8-sig", newline="") as handle:
                adjudication_rows = list(csv.DictReader(handle))
            adjudication_rows[0]["final_value"] = original_mapping
            adjudication_rows[0]["adjudicator_id"] = "human-c"
            bmr.write_csv(
                adjudication_path, bmr.ADJUDICATION_COLUMNS, adjudication_rows
            )

            reviewer_one_id, review_one = bmr.load_review(reviewer_one_path, packet)
            reviewer_two_id, review_two = bmr.load_review(reviewer_two_path, packet)
            consensus = bmr.consensus_review(
                packet,
                reviewer_one_id,
                review_one,
                reviewer_two_id,
                review_two,
                adjudication_path,
            )
            result, consensus_rows = bmr.sensitivity_analysis(
                packet,
                key,
                reviewer_one_id,
                review_one,
                reviewer_two_id,
                review_two,
                consensus,
            )
            self.assertEqual(len(consensus_rows), 384)
            self.assertEqual(
                result["auto_vs_human_consensus"]["mapping_disagreement_count"], 0
            )
            self.assertFalse(result["gate_flips"])
            self.assertFalse(result["decision"]["reviewer_reliability_warning"])
            self.assertEqual(
                result["decision"]["recommendation"],
                "proceed_to_gq2_generator_development",
            )

            output_dir = temp_dir / "analysis"
            bmr.write_analysis(output_dir, result, consensus_rows)
            self.assertTrue((output_dir / "sensitivity-results.json").exists())
            self.assertTrue((output_dir / "SENSITIVITY-REPORT.md").exists())
            saved = json.loads(
                (output_dir / "sensitivity-results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["packet_hash"], packet["packet_hash"])

    def test_incomplete_review_template_is_rejected(self) -> None:
        packet = bmr.load_packet(PACKET_PATH)
        with self.assertRaisesRegex(ValueError, "must be 0 or 1|invalid mapping"):
            bmr.load_review(PACKAGE_DIR / "forms" / "reviewer-1.csv", packet)


if __name__ == "__main__":
    unittest.main()
