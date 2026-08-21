from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import blind_mapping_review as bmr


ROOT = Path(__file__).resolve().parent
DEFAULT_PACKET = ROOT / "blind-review-v0.4" / "packet" / "blind-review-packet.json"
DEFAULT_KEY = ROOT / "blind-review-v0.4" / "coordinator" / "unblinding-key.json"
DEFAULT_OUTPUT = ROOT / "codex-direct-review-v0.4"
PROGRESS_FILE = "codex-direct-progress.json"
MODE_ID = "codex_direct_blind_mapping_v0.4"
SCHEDULE_SEED = 20260826


DIRECT_REVIEW_INSTRUCTION = """你是模式 2 的 Codex 直接条件盲评审。你不能查看 API 模式评分、原自动映射、生成方法、模型种子、隐藏事实、正确行动或下游成绩。

逐个候选判断：
1. mapped_evidence_id：只有某一个 E1-E6 能单独、完整回答候选要求的对象、比较、切分、时间和指标时才映射；需要组合多个目录项、只能部分回答或目录没有所需数据时必须是 NONE。
2. atomic_single_observation：只要求一个可独立回答的观察、比较或检验为 1，否则为 0。
3. fully_answerable_by_mapping：映射 E1-E6 时必须为 1，NONE 时必须为 0。
4. distinct_from_other_candidates：与同组其他候选在所需证据和机制检验上不重复为 1，否则为 0。
5. action_discriminating：至少一种合理答案可能改变两个以上公开行动的相对支持为 1，否则为 0。

不要选择“最接近”的目录项，不要推断目录未承诺的联表、交叉分组、额外字段或因果识别。把结果写成响应 JSON；不要让用户参与评分。"""


def normalize_binary(value: Any, label: str) -> str:
    if value in (0, "0", False):
        return "0"
    if value in (1, "1", True):
        return "1"
    raise ValueError(f"{label} must be 0 or 1")


def save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def initial_progress(packet: dict[str, Any]) -> dict[str, Any]:
    order = [unit["review_id"] for unit in packet["review_units"]]
    random.Random(SCHEDULE_SEED).shuffle(order)
    return {
        "schema_version": "1.0",
        "mode_id": MODE_ID,
        "packet_hash": packet["packet_hash"],
        "schedule_seed": SCHEDULE_SEED,
        "review_order": order,
        "completed": {},
    }


def load_progress(path: Path, packet: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("Codex direct progress does not exist; run prepare first")
    progress = bmr.load_json(path)
    expected = initial_progress(packet)
    for field in ("mode_id", "packet_hash", "schedule_seed", "review_order"):
        if progress.get(field) != expected[field]:
            raise ValueError(f"{path}: {field} differs from frozen Codex direct mode")
    if not isinstance(progress.get("completed"), dict):
        raise ValueError(f"{path}: completed must be an object")
    return progress


def prepare(packet_path: Path, output_dir: Path) -> dict[str, Any]:
    packet = bmr.load_packet(packet_path)
    progress_path = output_dir / PROGRESS_FILE
    if progress_path.exists():
        return load_progress(progress_path, packet)
    progress = initial_progress(packet)
    save_json_atomic(progress_path, progress)
    return progress


def next_review_id(progress: dict[str, Any]) -> str | None:
    return next(
        (
            review_id
            for review_id in progress["review_order"]
            if review_id not in progress["completed"]
        ),
        None,
    )


def task_for_next(
    packet: dict[str, Any], progress: dict[str, Any]
) -> dict[str, Any] | None:
    review_id = next_review_id(progress)
    if review_id is None:
        return None
    unit = next(
        unit for unit in packet["review_units"] if unit["review_id"] == review_id
    )
    return {
        "mode_id": MODE_ID,
        "packet_hash": packet["packet_hash"],
        "review_id": review_id,
        "instruction": DIRECT_REVIEW_INSTRUCTION,
        "public_case": unit["public_case"],
        "evidence_catalog": unit["evidence_catalog"],
        "candidates": unit["candidates"],
        "required_response_schema": {
            "packet_hash": packet["packet_hash"],
            "review_id": review_id,
            "processor_id": "codex",
            "direct_review": {
                "C1": {
                    "mapped_evidence_id": "E1 or NONE",
                    "atomic_single_observation": "0 or 1",
                    "fully_answerable_by_mapping": "0 or 1",
                    "distinct_from_other_candidates": "0 or 1",
                    "action_discriminating": "0 or 1",
                },
                "...": "repeat through C8",
            },
        },
    }


def parse_direct_response(
    payload: dict[str, Any],
    packet: dict[str, Any],
    unit: dict[str, Any],
) -> tuple[str, dict[str, dict[str, str]]]:
    required_top = {"packet_hash", "review_id", "processor_id", "direct_review"}
    if not isinstance(payload, dict) or set(payload) != required_top:
        raise ValueError("response must contain exactly four top-level fields")
    if payload["packet_hash"] != packet["packet_hash"]:
        raise ValueError("response packet hash differs")
    if payload["review_id"] != unit["review_id"]:
        raise ValueError("response review_id is not the current locked task")
    processor_id = str(payload["processor_id"]).strip()
    if not processor_id or "human" in processor_id.lower():
        raise ValueError("processor_id must identify Codex, not a human reviewer")
    raw_review = payload["direct_review"]
    expected_candidates = {candidate["id"] for candidate in unit["candidates"]}
    if not isinstance(raw_review, dict) or set(raw_review) != expected_candidates:
        raise ValueError("direct_review must contain exactly C1-C8")
    evidence_ids = {item["id"] for item in unit["evidence_catalog"]}
    parsed: dict[str, dict[str, str]] = {}
    for candidate_id in sorted(expected_candidates):
        raw = raw_review[candidate_id]
        if not isinstance(raw, dict) or set(raw) != set(bmr.REVIEW_FIELDS):
            raise ValueError(f"{candidate_id}: invalid review fields")
        mapping = str(raw["mapped_evidence_id"]).strip().upper()
        if mapping not in evidence_ids | {"NONE"}:
            raise ValueError(f"{candidate_id}: invalid mapping {mapping!r}")
        row = {"mapped_evidence_id": mapping}
        for field in bmr.BINARY_FIELDS:
            row[field] = normalize_binary(raw[field], f"{candidate_id}/{field}")
        if (mapping == "NONE") != (row["fully_answerable_by_mapping"] == "0"):
            raise ValueError(
                f"{candidate_id}: mapping and answerability are inconsistent"
            )
        parsed[candidate_id] = row
    return processor_id, parsed


def submit_response(
    packet_path: Path,
    output_dir: Path,
    response_path: Path,
) -> dict[str, Any]:
    packet = bmr.load_packet(packet_path)
    progress_path = output_dir / PROGRESS_FILE
    progress = load_progress(progress_path, packet)
    review_id = next_review_id(progress)
    if review_id is None:
        raise ValueError("Codex direct audit is already complete")
    unit = next(
        unit for unit in packet["review_units"] if unit["review_id"] == review_id
    )
    payload = bmr.load_json(response_path)
    processor_id, parsed = parse_direct_response(payload, packet, unit)
    if review_id in progress["completed"]:
        raise ValueError(f"{review_id}: locked review cannot be overwritten")
    progress["completed"][review_id] = {
        "processor_id": processor_id,
        "direct_review": parsed,
        "response_filename": response_path.name,
    }
    save_json_atomic(progress_path, progress)
    return progress


def progress_status(packet: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    complete = len(progress["completed"])
    total = packet["review_unit_count"]
    return {
        "mode_id": MODE_ID,
        "completed_units": complete,
        "total_units": total,
        "remaining_units": total - complete,
        "next_review_id": next_review_id(progress),
        "ready_to_finalize": complete == total,
    }


def locked_review(
    packet: dict[str, Any], progress: dict[str, Any]
) -> dict[tuple[str, str], dict[str, str]]:
    review: dict[tuple[str, str], dict[str, str]] = {}
    for unit in packet["review_units"]:
        review_id = unit["review_id"]
        rows = progress["completed"][review_id]["direct_review"]
        for candidate in unit["candidates"]:
            candidate_id = candidate["id"]
            review[(review_id, candidate_id)] = rows[candidate_id]
    return review


def neutralize_result_schema(result: dict[str, Any]) -> dict[str, Any]:
    result.pop("reviewers", None)
    result.pop("reviewer_agreement", None)
    result["original_auto_vs_codex_direct"] = result.pop("auto_vs_human_consensus")
    result["original_auto_gates"] = result.pop("auto_gates")
    result["codex_direct_gates"] = result.pop("human_consensus_gates")
    for comparison in result["condition_comparison"].values():
        comparison["original_auto"] = comparison.pop("auto")
        comparison["codex_direct"] = comparison.pop("human_consensus")
        comparison["delta_codex_minus_original_auto"] = comparison.pop(
            "delta_human_minus_auto"
        )
        comparison["original_auto_vs_codex_mapping_disagreement_count"] = (
            comparison.pop("auto_vs_human_mapping_disagreement_count")
        )
        comparison["original_auto_vs_codex_mapping_disagreement_rate"] = (
            comparison.pop("auto_vs_human_mapping_disagreement_rate")
        )
        comparison["codex_quality_rates"] = comparison.pop("human_quality_rates")
    for item in result["gate_flips"]:
        item["original_auto"] = item.pop("auto")
        item["codex_direct"] = item.pop("human_consensus")
    result["decision"].pop("reviewer_reliability_warning", None)
    return result


def direct_report(result: dict[str, Any]) -> str:
    disagreement = result["original_auto_vs_codex_direct"]
    lines = [
        "# Candidate Generation v0.4：Codex 直接盲审报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        "## 决策结论",
        "",
        f"- 建议：`{result['decision']['recommendation']}`",
        f"- 原自动匹配与 Codex 直接评分不一致率：`{disagreement['mapping_disagreement_rate']:.3f}`（{disagreement['mapping_disagreement_count']}/{disagreement['mapping_rows']}）。",
        f"- 预注册推进门槛翻转：`{len(result['gate_flips'])}` 项。",
        f"- 直接处理器：`{json.dumps(result['codex_direct']['processor_ids'], ensure_ascii=False)}`。",
        "",
        "## 分条件结果",
        "",
        "| 条件 | 原自动全覆盖 | Codex 全覆盖 | 原自动匹配率 | Codex 匹配率 | 映射差异率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in bmr.GENERATOR_CONDITIONS:
        row = result["condition_comparison"][condition]
        original = row["original_auto"]
        direct = row["codex_direct"]
        lines.append(
            f"| {condition} | {original['both_branches_full_critical_coverage_count']:.0f}/12 | {direct['both_branches_full_critical_coverage_count']:.0f}/12 | {original['catalog_match_rate']:.3f} | {direct['catalog_match_rate']:.3f} | {row['original_auto_vs_codex_mapping_disagreement_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 本模式由当前 Codex 直接处理，不调用外部模型 API，也没有人工评分步骤。",
            "- Codex 在同一任务环境中连续处理多个单元，不能把内部一致性称为独立评审者可靠性或外部金标准。",
            "- API 模式结果在全部直接评分锁定前不可见；跨模式比较只能在事后进行。",
            "",
        ]
    )
    return "\n".join(lines)


def finalize(
    packet_path: Path,
    key_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    packet = bmr.load_packet(packet_path)
    progress = load_progress(output_dir / PROGRESS_FILE, packet)
    if len(progress["completed"]) != packet["review_unit_count"]:
        raise ValueError("Codex direct audit is incomplete")
    review = locked_review(packet, progress)
    key = bmr.load_json(key_path)
    result, consensus_rows = bmr.sensitivity_analysis(
        packet,
        key,
        "codex-direct-locked-copy-a",
        review,
        "codex-direct-locked-copy-b",
        review,
        review,
    )
    result = neutralize_result_schema(result)
    result["decision"]["recommendation"] = (
        "fix_mapping_interface_before_gq2"
        if result["decision"]["material_mapping_issue"]
        else "proceed_to_gq2_generator_development"
    )
    result["mode_id"] = MODE_ID
    result["codex_direct"] = {
        "external_api_call_count": 0,
        "human_review_count": 0,
        "processor_ids": sorted(
            {
                state["processor_id"]
                for state in progress["completed"].values()
            }
        ),
        "completed_units": len(progress["completed"]),
    }
    for row in consensus_rows:
        row["original_auto_mapping"] = row.pop("auto_mapping")

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = bmr.review_template_rows(packet, "codex-direct")
    for row in rows:
        row.update(review[(row["review_id"], row["candidate_id"])])
    bmr.write_csv(output_dir / "codex-direct-final.csv", bmr.REVIEW_COLUMNS, rows)
    export_columns = (
        "review_id",
        "pair_id",
        "generator",
        "model_seed",
        "candidate_id",
        "original_auto_mapping",
        *bmr.REVIEW_FIELDS,
    )
    bmr.write_csv(output_dir / "consensus-mappings.csv", export_columns, consensus_rows)
    (output_dir / "codex-direct-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "CODEX-DIRECT-REPORT.md").write_text(
        direct_report(result), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Coordinate Codex direct blind audit")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "next", "status"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
        subparser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    submit_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    submit_parser.add_argument("--response", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    finalize_parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    finalize_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.command == "prepare":
        packet = bmr.load_packet(args.packet)
        progress = prepare(args.packet, args.output)
        print(json.dumps(progress_status(packet, progress), ensure_ascii=False, indent=2))
    elif args.command == "next":
        packet = bmr.load_packet(args.packet)
        progress = load_progress(args.output / PROGRESS_FILE, packet)
        task = task_for_next(packet, progress)
        print(json.dumps(task or {"status": "complete"}, ensure_ascii=False, indent=2))
    elif args.command == "status":
        packet = bmr.load_packet(args.packet)
        progress = load_progress(args.output / PROGRESS_FILE, packet)
        print(json.dumps(progress_status(packet, progress), ensure_ascii=False, indent=2))
    elif args.command == "submit":
        progress = submit_response(
            args.packet, args.output, args.response
        )
        packet = bmr.load_packet(args.packet)
        print(json.dumps(progress_status(packet, progress), ensure_ascii=False, indent=2))
    else:
        result = finalize(args.packet, args.key, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
