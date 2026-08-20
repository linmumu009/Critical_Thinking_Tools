from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_MODE_1_RESULT = (
    ROOT / "automated-review-v0.4" / "automated-sensitivity-results.json"
)
DEFAULT_MODE_1_MAPPINGS = (
    ROOT / "automated-review-v0.4" / "consensus-mappings.csv"
)
DEFAULT_MODE_2_RESULT = (
    ROOT / "automated-review-mode-2-v0.4" / "mode-2-results.json"
)
DEFAULT_MODE_2_MAPPINGS = (
    ROOT / "automated-review-mode-2-v0.4" / "consensus-mappings.csv"
)
DEFAULT_OUTPUT = ROOT / "automated-mode-comparison-v0.4"
REVIEW_FIELDS = (
    "mapped_evidence_id",
    "atomic_single_observation",
    "fully_answerable_by_mapping",
    "distinct_from_other_candidates",
    "action_discriminating",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_mappings(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            key = (raw["review_id"], raw["candidate_id"])
            if key in rows:
                raise ValueError(f"{path}: duplicate row {key}")
            rows[key] = raw
    return rows


def gate_differences(
    mode_1: dict[str, Any], mode_2: dict[str, Any]
) -> list[dict[str, Any]]:
    left = mode_1["automated_consensus_gates"]
    right = mode_2["mode_2_consensus_gates"]
    if set(left) != set(right):
        raise ValueError("mode gate conditions differ")
    differences: list[dict[str, Any]] = []
    for condition in sorted(left):
        if set(left[condition]) != set(right[condition]):
            raise ValueError(f"{condition}: mode gate fields differ")
        for gate in sorted(left[condition]):
            if left[condition][gate] != right[condition][gate]:
                differences.append(
                    {
                        "condition": condition,
                        "gate": gate,
                        "mode_1": left[condition][gate],
                        "mode_2": right[condition][gate],
                    }
                )
    return differences


def compare_modes(
    mode_1_result: dict[str, Any],
    mode_1_rows: dict[tuple[str, str], dict[str, str]],
    mode_2_result: dict[str, Any],
    mode_2_rows: dict[tuple[str, str], dict[str, str]],
) -> dict[str, Any]:
    if mode_1_result.get("status") != "complete":
        raise ValueError("API mode 1 result is incomplete")
    if mode_2_result.get("status") != "complete":
        raise ValueError("API mode 2 result is incomplete")
    if mode_2_result.get("mode_id") != "api_mode_2_contract_proof_falsifier_v0.4":
        raise ValueError("unexpected API mode 2 identity")
    if mode_1_result["packet_hash"] != mode_2_result["packet_hash"]:
        raise ValueError("mode packet hashes differ")
    if set(mode_1_rows) != set(mode_2_rows):
        raise ValueError("mode mapping rows differ")

    totals: Counter[str] = Counter()
    agreements: Counter[str] = Counter()
    by_condition: dict[str, Counter[str]] = defaultdict(Counter)
    for key in sorted(mode_1_rows):
        left = mode_1_rows[key]
        right = mode_2_rows[key]
        if left["generator"] != right["generator"]:
            raise ValueError(f"{key}: generator differs")
        condition = left["generator"]
        for field in REVIEW_FIELDS:
            totals[field] += 1
            agreements[field] += left[field] == right[field]
        by_condition[condition]["rows"] += 1
        by_condition[condition]["mapping_agreements"] += (
            left["mapped_evidence_id"] == right["mapped_evidence_id"]
        )

    mode_1_models = {
        params.get("model_name")
        for params in mode_1_result.get("automated_audit", {})
        .get("api_parameters", {})
        .values()
        if params.get("model_name")
    }
    mode_2_models = {
        params.get("model_name")
        for params in mode_2_result.get("mode_2_pipeline", {})
        .get("api_parameters", {})
        .values()
        if params.get("model_name")
    }

    return {
        "status": "complete",
        "comparison_type": "post_lock_read_only",
        "packet_hash": mode_1_result["packet_hash"],
        "mapping_rows": len(mode_1_rows),
        "field_agreement": {
            field: {
                "agreement_count": agreements[field],
                "agreement_rate": agreements[field] / totals[field],
            }
            for field in REVIEW_FIELDS
        },
        "condition_mapping_agreement": {
            condition: {
                "rows": counts["rows"],
                "agreement_count": counts["mapping_agreements"],
                "agreement_rate": counts["mapping_agreements"] / counts["rows"],
            }
            for condition, counts in sorted(by_condition.items())
        },
        "gate_differences": gate_differences(mode_1_result, mode_2_result),
        "recommendations": {
            "mode_1": mode_1_result["decision"]["recommendation"],
            "mode_2": mode_2_result["decision"]["recommendation"],
        },
        "shared_model_names": sorted(mode_1_models & mode_2_models),
        "interpretation_boundary": (
            "Agreement measures pipeline stability after both modes are locked; "
            "it is not a vote for ground truth and cannot modify either mode."
        ),
    }


def comparison_report(result: dict[str, Any]) -> str:
    mapping = result["field_agreement"]["mapped_evidence_id"]
    lines = [
        "# API 模式 1 / 模式 2 只读比较报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        f"- 映射一致率：`{mapping['agreement_rate']:.3f}`（{mapping['agreement_count']}/{result['mapping_rows']}）。",
        f"- 推进门槛差异：`{len(result['gate_differences'])}` 项。",
        f"- 模式 1 建议：`{result['recommendations']['mode_1']}`。",
        f"- 模式 2 建议：`{result['recommendations']['mode_2']}`。",
        f"- 两种模式重叠的模型名：`{json.dumps(result['shared_model_names'], ensure_ascii=False)}`。",
        "",
        "## 分条件映射一致率",
        "",
        "| 条件 | 一致 | 总数 | 一致率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for condition, row in result["condition_mapping_agreement"].items():
        lines.append(
            f"| {condition} | {row['agreement_count']} | {row['rows']} | {row['agreement_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 比较器只读取两边已锁定的最终工件，不参与任何 API 评分。",
            "- 一致率衡量两条自动管线的稳定性，不是真值投票，也不能反向修改任一模式。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two locked API audit modes")
    parser.add_argument("--mode-1-result", type=Path, default=DEFAULT_MODE_1_RESULT)
    parser.add_argument("--mode-1-mappings", type=Path, default=DEFAULT_MODE_1_MAPPINGS)
    parser.add_argument("--mode-2-result", type=Path, default=DEFAULT_MODE_2_RESULT)
    parser.add_argument("--mode-2-mappings", type=Path, default=DEFAULT_MODE_2_MAPPINGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = compare_modes(
        load_json(args.mode_1_result),
        load_mappings(args.mode_1_mappings),
        load_json(args.mode_2_result),
        load_mappings(args.mode_2_mappings),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "mode-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "MODE-COMPARISON-REPORT.md").write_text(
        comparison_report(result), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
