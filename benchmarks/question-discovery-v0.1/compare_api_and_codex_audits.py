from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_API_RESULT = (
    ROOT / "automated-review-v0.4" / "automated-sensitivity-results.json"
)
DEFAULT_API_MAPPINGS = ROOT / "automated-review-v0.4" / "consensus-mappings.csv"
DEFAULT_CODEX_RESULT = (
    ROOT / "codex-direct-review-v0.4" / "codex-direct-results.json"
)
DEFAULT_CODEX_MAPPINGS = (
    ROOT / "codex-direct-review-v0.4" / "consensus-mappings.csv"
)
DEFAULT_OUTPUT = ROOT / "api-codex-comparison-v0.4"
FIELDS = (
    "mapped_evidence_id",
    "atomic_single_observation",
    "fully_answerable_by_mapping",
    "distinct_from_other_candidates",
    "action_discriminating",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            key = (raw["review_id"], raw["candidate_id"])
            if key in rows:
                raise ValueError(f"{path}: duplicate row {key}")
            rows[key] = raw
    return rows


def compare(
    api_result: dict[str, Any],
    api_rows: dict[tuple[str, str], dict[str, str]],
    codex_result: dict[str, Any],
    codex_rows: dict[tuple[str, str], dict[str, str]],
) -> dict[str, Any]:
    if api_result.get("status") != "complete":
        raise ValueError("API mode is incomplete")
    if codex_result.get("status") != "complete":
        raise ValueError("Codex direct mode is incomplete")
    if codex_result.get("mode_id") != "codex_direct_blind_mapping_v0.4":
        raise ValueError("unexpected Codex direct mode identity")
    if api_result["packet_hash"] != codex_result["packet_hash"]:
        raise ValueError("mode packet hashes differ")
    if set(api_rows) != set(codex_rows):
        raise ValueError("mode rows differ")

    agreements: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    by_condition: dict[str, Counter[str]] = defaultdict(Counter)
    for key in sorted(api_rows):
        left = api_rows[key]
        right = codex_rows[key]
        if left["generator"] != right["generator"]:
            raise ValueError(f"{key}: generator differs")
        condition = left["generator"]
        for field in FIELDS:
            totals[field] += 1
            agreements[field] += left[field] == right[field]
        by_condition[condition]["rows"] += 1
        by_condition[condition]["mapping_agreements"] += (
            left["mapped_evidence_id"] == right["mapped_evidence_id"]
        )

    api_gates = api_result["automated_consensus_gates"]
    codex_gates = codex_result["codex_direct_gates"]
    gate_differences: list[dict[str, Any]] = []
    if set(api_gates) != set(codex_gates):
        raise ValueError("mode gate conditions differ")
    for condition in sorted(api_gates):
        if set(api_gates[condition]) != set(codex_gates[condition]):
            raise ValueError(f"{condition}: mode gate fields differ")
        for gate in sorted(api_gates[condition]):
            if api_gates[condition][gate] != codex_gates[condition][gate]:
                gate_differences.append(
                    {
                        "condition": condition,
                        "gate": gate,
                        "api": api_gates[condition][gate],
                        "codex_direct": codex_gates[condition][gate],
                    }
                )

    return {
        "status": "complete",
        "comparison_type": "post_lock_read_only",
        "packet_hash": api_result["packet_hash"],
        "mapping_rows": len(api_rows),
        "field_agreement": {
            field: {
                "agreement_count": agreements[field],
                "agreement_rate": agreements[field] / totals[field],
            }
            for field in FIELDS
        },
        "condition_mapping_agreement": {
            condition: {
                "rows": counts["rows"],
                "agreement_count": counts["mapping_agreements"],
                "agreement_rate": counts["mapping_agreements"] / counts["rows"],
            }
            for condition, counts in sorted(by_condition.items())
        },
        "gate_differences": gate_differences,
        "recommendations": {
            "api": api_result["decision"]["recommendation"],
            "codex_direct": codex_result["decision"]["recommendation"],
        },
        "interpretation_boundary": (
            "Agreement measures backend sensitivity after both modes are locked; "
            "it is not a truth vote and cannot modify either mode."
        ),
    }


def report(result: dict[str, Any]) -> str:
    mapping = result["field_agreement"]["mapped_evidence_id"]
    lines = [
        "# API / Codex 直接模式只读比较报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        f"- 映射一致率：`{mapping['agreement_rate']:.3f}`（{mapping['agreement_count']}/{result['mapping_rows']}）。",
        f"- 推进门槛差异：`{len(result['gate_differences'])}` 项。",
        f"- API 建议：`{result['recommendations']['api']}`。",
        f"- Codex 直接建议：`{result['recommendations']['codex_direct']}`。",
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
            "- 比较器只读取两边已锁定结果，不参与 API 或 Codex 评分。",
            "- 一致率衡量执行后端敏感性，不是真值投票，也不能反向修改任一结果。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare locked API and Codex audits")
    parser.add_argument("--api-result", type=Path, default=DEFAULT_API_RESULT)
    parser.add_argument("--api-mappings", type=Path, default=DEFAULT_API_MAPPINGS)
    parser.add_argument("--codex-result", type=Path, default=DEFAULT_CODEX_RESULT)
    parser.add_argument("--codex-mappings", type=Path, default=DEFAULT_CODEX_MAPPINGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = compare(
        load_json(args.api_result),
        load_rows(args.api_mappings),
        load_json(args.codex_result),
        load_rows(args.codex_mappings),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "api-codex-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "API-CODEX-COMPARISON-REPORT.md").write_text(
        report(result), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
