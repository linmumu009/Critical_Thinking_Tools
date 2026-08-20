from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import api_audit
import automated_mapping_audit_mode_2 as mode_2
import blind_mapping_review as bmr
import compare_api_audit_modes as compare_modes


ROOT = Path(__file__).resolve().parent
PACKET_PATH = ROOT / "blind-review-v0.4" / "packet" / "blind-review-packet.json"
KEY_PATH = ROOT / "blind-review-v0.4" / "coordinator" / "unblinding-key.json"


class ApiAuditModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packet = bmr.load_packet(PACKET_PATH)
        cls.key = bmr.load_json(KEY_PATH)
        cls.unit = cls.packet["review_units"][0]
        cls.key_by_id = {
            item["review_id"]: item for item in cls.key["review_units"]
        }

    def contract_fixture(self, unit: dict) -> dict[str, dict]:
        return {
            candidate["id"]: {
                "requirements": ["一个锁定的测试要求"],
                "atomic_single_observation": "1",
                "distinct_from_other_candidates": "1",
                "action_discriminating": "1",
            }
            for candidate in unit["candidates"]
        }

    def proof_fixture(self, unit: dict) -> dict[str, dict]:
        matches = self.key_by_id[unit["review_id"]]["auto_matches"]
        proofs: dict[str, dict] = {}
        evidence_ids = [item["id"] for item in unit["evidence_catalog"]]
        for candidate in unit["candidates"]:
            candidate_id = candidate["id"]
            mapping = matches[candidate_id]
            proofs[candidate_id] = {
                "mapped_evidence_id": mapping,
                "coverage_by_evidence": {
                    evidence_id: [1] if evidence_id == mapping else []
                    for evidence_id in evidence_ids
                },
            }
        return proofs

    def verdict_fixture(
        self, unit: dict, proofs: dict[str, dict]
    ) -> dict[str, dict[str, str]]:
        return {
            candidate["id"]: {
                "contract_faithful": "1",
                "proof_valid": "1",
                "final_mapped_evidence_id": proofs[candidate["id"]][
                    "mapped_evidence_id"
                ],
                "atomic_single_observation": "1",
                "distinct_from_other_candidates": "1",
                "action_discriminating": "1",
                "reason_code": "ACCEPT",
            }
            for candidate in unit["candidates"]
        }

    def test_launcher_dispatches_modes_to_separate_process_entrypoints(self) -> None:
        mode_1 = api_audit.dispatch_command("1", "run", ["--output", "one"])
        mode_2_command = api_audit.dispatch_command(
            "2", "run", ["--output", "two"]
        )
        self.assertIn("automated_mapping_audit.py", mode_1[2])
        self.assertIn("automated_mapping_audit_mode_2.py", mode_2_command[2])
        self.assertNotEqual(mode_1[2], mode_2_command[2])
        source = (ROOT / "automated_mapping_audit_mode_2.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("automated-review-v0.4", source)
        self.assertNotIn("import automated_mapping_audit", source)

    def test_mode_2_stage_visibility_is_separated(self) -> None:
        extractor_payload = json.loads(mode_2.extractor_messages(self.unit)[1]["content"])
        self.assertNotIn("evidence_catalog", extractor_payload)
        self.assertNotIn(
            "evidence_capabilities", extractor_payload["public_case"]
        )
        contracts = self.contract_fixture(self.unit)
        prover_payload = json.loads(
            mode_2.prover_messages(self.unit, contracts)[1]["content"]
        )
        self.assertNotIn("candidates", prover_payload)
        self.assertIn("evidence_catalog", prover_payload)
        proofs = self.proof_fixture(self.unit)
        falsifier_payload = json.loads(
            mode_2.falsifier_messages(self.unit, contracts, proofs)[1]["content"]
        )
        self.assertIn("candidates", falsifier_payload)
        self.assertIn("proofs", falsifier_payload)
        serialized = json.dumps(
            [extractor_payload, prover_payload, falsifier_payload],
            ensure_ascii=False,
        )
        for forbidden in ("auto_matches", "generator", "model_seed", "oracle_facts"):
            self.assertNotIn(forbidden, serialized)

    def test_mode_2_requires_dedicated_api_configuration(self) -> None:
        main_only = {
            "url": "https://main.invalid/v1",
            "api_key": "main-secret",
            "model_name": "main-model",
        }
        with self.assertRaisesRegex(ValueError, "api_audit_mode=2"):
            mode_2.mode_2_role_config(main_only, "extractor")
        base = {
            "api_audit_mode": 2,
            "url": "https://mode2.invalid/v1",
            "api_key": "mode2-secret",
            "model_name": "mode2-model",
        }
        config = mode_2.mode_2_role_config(base, "extractor")
        self.assertEqual(config["url"], "https://mode2.invalid/v1")
        self.assertEqual(config["model_name"], "mode2-model")
        self.assertFalse(config["enable_thinking"])
        safe = mode_2.safe_role_config(config)
        self.assertNotIn("url", safe)
        self.assertNotIn("api_key", safe)
        self.assertNotEqual(
            mode_2.DEFAULT_CONFIG.name, "model-config.local.json"
        )

    def test_mode_2_parsers_enforce_contract_proof_and_veto_rules(self) -> None:
        contracts = self.contract_fixture(self.unit)
        parsed_contracts = mode_2.parse_contracts(
            "CONTRACTS: " + json.dumps(contracts, ensure_ascii=False), self.unit
        )
        proofs = self.proof_fixture(self.unit)
        parsed_proofs = mode_2.parse_proofs(
            "PROOFS: " + json.dumps(proofs), self.unit, parsed_contracts
        )
        invalid_proofs = json.loads(json.dumps(proofs))
        fully_mapped = next(
            candidate_id
            for candidate_id, proof in invalid_proofs.items()
            if proof["mapped_evidence_id"] != "NONE"
        )
        invalid_proofs[fully_mapped]["mapped_evidence_id"] = "NONE"
        with self.assertRaisesRegex(ValueError, "full coverage proof"):
            mode_2.parse_proofs(
                "PROOFS: " + json.dumps(invalid_proofs),
                self.unit,
                parsed_contracts,
            )
        verdicts = self.verdict_fixture(self.unit, proofs)
        self.assertEqual(
            mode_2.parse_verdicts(
                "VERDICTS: " + json.dumps(verdicts), self.unit, parsed_proofs
            ),
            verdicts,
        )
        candidate_id = next(iter(verdicts))
        verdicts[candidate_id]["contract_faithful"] = "0"
        verdicts[candidate_id]["reason_code"] = "CONTRACT_OMISSION"
        with self.assertRaisesRegex(ValueError, "rejected verdict must map NONE"):
            mode_2.parse_verdicts(
                "VERDICTS: " + json.dumps(verdicts), self.unit, parsed_proofs
            )

    def test_mode_2_finalize_full_fixture_without_api(self) -> None:
        configs = {
            role: {
                "model_name": f"mode2-{role}",
                "temperature": 0,
                "max_tokens": 3072,
                "enable_thinking": False,
            }
            for role in mode_2.ROLE_SEED_BASES
        }
        progress = mode_2.initial_progress(self.packet, configs)
        for unit in self.packet["review_units"]:
            contracts = self.contract_fixture(unit)
            proofs = self.proof_fixture(unit)
            verdicts = self.verdict_fixture(unit, proofs)
            progress["completed"][unit["review_id"]] = {
                "extractor": {
                    "parsed": contracts,
                    "raw": "fixture",
                    "model_call_count": 1,
                    "protocol_deviations": [],
                },
                "prover": {
                    "parsed": proofs,
                    "raw": "fixture",
                    "model_call_count": 1,
                    "protocol_deviations": [],
                },
                "falsifier": {
                    "parsed": verdicts,
                    "raw": "fixture",
                    "model_call_count": 1,
                    "protocol_deviations": [],
                },
            }
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            output = Path(raw_temp_dir)
            mode_2.save_progress(output / mode_2.PROGRESS_FILE, progress)
            result = mode_2.finalize_mode_2(PACKET_PATH, KEY_PATH, output)
            self.assertEqual(result["mode_id"], mode_2.MODE_ID)
            self.assertEqual(
                result["original_auto_vs_mode_2_consensus"][
                    "mapping_disagreement_count"
                ],
                0,
            )
            self.assertEqual(
                result["decision"]["recommendation"],
                "proceed_to_gq2_generator_development",
            )
            self.assertTrue((output / "MODE-2-REPORT.md").exists())
            self.assertTrue((output / "contracts.json").exists())

    def test_read_only_comparator_does_not_modify_mode_results(self) -> None:
        gates = {"G0": {"passed": False}}
        mode_1_result = {
            "status": "complete",
            "packet_hash": "hash",
            "automated_consensus_gates": gates,
            "decision": {"recommendation": "fix_mapping_interface_before_gq2"},
        }
        mode_2_result = {
            "status": "complete",
            "mode_id": mode_2.MODE_ID,
            "packet_hash": "hash",
            "mode_2_consensus_gates": gates,
            "decision": {"recommendation": "fix_mapping_interface_before_gq2"},
        }
        row = {
            "generator": "G0",
            "mapped_evidence_id": "E1",
            "atomic_single_observation": "1",
            "fully_answerable_by_mapping": "1",
            "distinct_from_other_candidates": "1",
            "action_discriminating": "1",
        }
        left = {("R001", "C1"): dict(row)}
        right = {("R001", "C1"): dict(row)}
        result = compare_modes.compare_modes(
            mode_1_result, left, mode_2_result, right
        )
        self.assertEqual(
            result["field_agreement"]["mapped_evidence_id"]["agreement_rate"],
            1,
        )
        self.assertEqual(result["comparison_type"], "post_lock_read_only")
        self.assertEqual(result["gate_differences"], [])


if __name__ == "__main__":
    unittest.main()
