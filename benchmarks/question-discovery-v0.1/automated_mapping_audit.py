from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import benchmark
import blind_mapping_review as bmr


ROOT = Path(__file__).resolve().parent
DEFAULT_PACKET = ROOT / "blind-review-v0.4" / "packet" / "blind-review-packet.json"
DEFAULT_KEY = ROOT / "blind-review-v0.4" / "coordinator" / "unblinding-key.json"
DEFAULT_CONFIG = ROOT / "model-config.local.json"
DEFAULT_OUTPUT = ROOT / "automated-review-v0.4"
PROGRESS_FILE = "automated-audit-progress.json"
SCHEDULE_SEED = 20260824
ROLE_SEED_BASES = {"judge_a": 610000, "judge_b": 620000, "arbitrator": 630000}


COMMON_RUBRIC = """你在做候选问题到证据目录的条件盲审计。你不知道候选由什么方法生成，也看不到答案、隐藏分支、证据重要性、正确行动或下游成绩。

逐个候选输出五个字段：
1. mapped_evidence_id：只有某一个 E1-E6 能单独、完整回答候选所要求的对象、比较、切分、时间和指标时才映射；需要组合多个目录项、只能部分回答或目录没有所需数据时必须是 NONE。
2. atomic_single_observation：只要求一个可独立回答的观察、比较或检验为 1，否则为 0。
3. fully_answerable_by_mapping：mapped_evidence_id 为 E1-E6 时必须为 1，为 NONE 时必须为 0。
4. distinct_from_other_candidates：与同组其他候选在所需证据和机制检验上不重复为 1，实质重复为 0。
5. action_discriminating：至少一种合理答案可能改变两个以上公开行动的相对支持为 1，否则为 0。

不要选择“最接近”的目录项，不要推断目录未承诺的联表、交叉分组、因果识别或额外字段。严格只输出：
AUDIT: {"C1":{"mapped_evidence_id":"E1或NONE","atomic_single_observation":0或1,"fully_answerable_by_mapping":0或1,"distinct_from_other_candidates":0或1,"action_discriminating":0或1},...,"C8":{...}}"""


JUDGE_PROMPTS = {
    "judge_a": COMMON_RUBRIC
    + "\n\n你的角色是严格的证据契约审计员。优先防止把部分相关误判为完整可答；逐字检查候选要求与目录承诺是否一致。",
    "judge_b": COMMON_RUBRIC
    + "\n\n你的角色是独立的反例审计员。先尝试构造一个目录项无法完整回答候选的反例，再决定映射；同时警惕把措辞变化误当成机制独立。不要参考任何其他评审。",
}


ARBITRATOR_PROMPT = """你是条件盲仲裁器。你只会看到两位自动评审不一致的字段、公开案例、证据目录和候选问题；看不到原自动匹配、生成方法、种子、隐藏事实、正确行动或下游结果。

按以下固定标准逐项裁决：单个目录项必须完整回答候选，否则映射 NONE；原子性只看是否一个独立观察；同组独立性看证据与机制而非措辞；行动判别性看合理答案是否可能改变公开行动相对支持。不得修改双方一致的字段。

严格只输出：
DECISIONS: [{"candidate_id":"C1","field":"mapped_evidence_id","final_value":"E1或NONE"}, ...]
输出必须恰好覆盖输入中的每一个分歧，不能增加、删除或改名。"""


def extract_marker_json(text: str, marker: str) -> Any:
    match = re.search(rf"{re.escape(marker)}\s*:\s*", text, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"missing {marker} marker")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text[match.end() :].lstrip())
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON after {marker}: {error}") from error
    return value


def normalize_binary(value: Any, label: str) -> str:
    if value in (0, "0", False):
        return "0"
    if value in (1, "1", True):
        return "1"
    raise ValueError(f"{label} must be 0 or 1")


def parse_audit(text: str, unit: dict[str, Any]) -> dict[str, dict[str, str]]:
    payload = extract_marker_json(text, "AUDIT")
    if not isinstance(payload, dict):
        raise ValueError("AUDIT must be a JSON object")
    expected_candidates = {item["id"] for item in unit["candidates"]}
    if set(payload) != expected_candidates:
        raise ValueError("AUDIT must contain exactly C1-C8")
    evidence_ids = {item["id"] for item in unit["evidence_catalog"]}
    parsed: dict[str, dict[str, str]] = {}
    for candidate_id in sorted(expected_candidates):
        raw = payload[candidate_id]
        if not isinstance(raw, dict) or set(raw) != set(bmr.REVIEW_FIELDS):
            raise ValueError(f"{candidate_id} must contain exactly the five review fields")
        mapping = str(raw["mapped_evidence_id"]).strip().upper()
        if mapping not in evidence_ids | {"NONE"}:
            raise ValueError(f"{candidate_id}: invalid mapping {mapping!r}")
        row = {"mapped_evidence_id": mapping}
        for field in bmr.BINARY_FIELDS:
            row[field] = normalize_binary(raw[field], f"{candidate_id}/{field}")
        if (mapping == "NONE") != (row["fully_answerable_by_mapping"] == "0"):
            raise ValueError(f"{candidate_id}: mapping and answerability are inconsistent")
        parsed[candidate_id] = row
    return parsed


def disagreement_items(
    judge_a: dict[str, dict[str, str]], judge_b: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for candidate_id in sorted(judge_a):
        for field in bmr.REVIEW_FIELDS:
            if judge_a[candidate_id][field] != judge_b[candidate_id][field]:
                items.append(
                    {
                        "candidate_id": candidate_id,
                        "field": field,
                        "judge_a_value": judge_a[candidate_id][field],
                        "judge_b_value": judge_b[candidate_id][field],
                    }
                )
    return items


def parse_decisions(
    text: str, unit: dict[str, Any], disagreements: list[dict[str, str]]
) -> list[dict[str, str]]:
    payload = extract_marker_json(text, "DECISIONS")
    if not isinstance(payload, list):
        raise ValueError("DECISIONS must be a JSON array")
    expected = {(item["candidate_id"], item["field"]) for item in disagreements}
    evidence_ids = {item["id"] for item in unit["evidence_catalog"]}
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in payload:
        if not isinstance(raw, dict) or set(raw) != {
            "candidate_id",
            "field",
            "final_value",
        }:
            raise ValueError("each decision must contain candidate_id, field, final_value")
        key = (str(raw["candidate_id"]), str(raw["field"]))
        if key not in expected or key in seen:
            raise ValueError(f"unexpected or duplicate arbitration decision: {key}")
        value = str(raw["final_value"]).strip().upper()
        if not bmr.valid_final_value(key[1], value, evidence_ids):
            raise ValueError(f"invalid final value for {key}: {value!r}")
        seen.add(key)
        parsed.append(
            {"candidate_id": key[0], "field": key[1], "final_value": value}
        )
    if seen != expected:
        raise ValueError("arbitration must resolve every disagreement exactly once")
    return parsed


def role_config(base: dict[str, Any], role: str) -> dict[str, Any]:
    config = dict(base)
    prefix = f"audit_{role}_"
    for field in ("url", "api_key", "model_name"):
        if base.get(prefix + field):
            config[field] = base[prefix + field]
    config["temperature"] = 0
    config["max_tokens"] = int(base.get("audit_max_tokens", base.get("max_tokens", 1536)))
    config["thinking_budget"] = int(
        base.get("audit_thinking_budget", base.get("thinking_budget", 512))
    )
    return config


def safe_role_config(config: dict[str, Any]) -> dict[str, Any]:
    return benchmark.api_runtime_parameters(config)


def unit_payload(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_id": unit["review_id"],
        "public_case": unit["public_case"],
        "evidence_catalog": unit["evidence_catalog"],
        "candidates": unit["candidates"],
    }


def judge_messages(unit: dict[str, Any], role: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": JUDGE_PROMPTS[role]},
        {
            "role": "user",
            "content": json.dumps(unit_payload(unit), ensure_ascii=False, indent=2),
        },
    ]


def arbitrator_messages(
    unit: dict[str, Any],
    disagreements: list[dict[str, str]],
    unit_index: int,
) -> tuple[list[dict[str, str]], list[str]]:
    order = ["judge_a", "judge_b"]
    random.Random(SCHEDULE_SEED + unit_index).shuffle(order)
    labeled = []
    for item in disagreements:
        labeled.append(
            {
                "candidate_id": item["candidate_id"],
                "field": item["field"],
                "judge_x": item[f"{order[0]}_value"],
                "judge_y": item[f"{order[1]}_value"],
            }
        )
    payload = {**unit_payload(unit), "disagreements": labeled}
    return [
        {"role": "system", "content": ARBITRATOR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ], order


def call_with_one_format_repair(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    seed: int,
    parser: Callable[[str], Any],
    repair_instruction: str,
) -> tuple[Any, str, int, list[dict[str, str]]]:
    raw = benchmark.api_chat_completion(config, messages, seed)
    try:
        return parser(raw), raw, 1, []
    except ValueError as first_error:
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": repair_instruction + f"\n格式错误：{first_error}",
            },
        ]
        repaired = benchmark.api_chat_completion(config, repair_messages, seed)
        parsed = parser(repaired)
        return (
            parsed,
            repaired,
            2,
            [{"type": "format_repair", "error": str(first_error), "raw": raw}],
        )


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def initial_progress(
    packet: dict[str, Any], configs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    order = [unit["review_id"] for unit in packet["review_units"]]
    random.Random(SCHEDULE_SEED).shuffle(order)
    return {
        "schema_version": "1.0",
        "packet_hash": packet["packet_hash"],
        "schedule_seed": SCHEDULE_SEED,
        "review_order": order,
        "api_parameters": {
            role: safe_role_config(config) for role, config in configs.items()
        },
        "completed": {},
        "failures": [],
    }


def load_or_create_progress(
    path: Path, packet: dict[str, Any], configs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    expected = initial_progress(packet, configs)
    if not path.exists():
        save_progress(path, expected)
        return expected
    progress = bmr.load_json(path)
    for field in ("packet_hash", "schedule_seed", "review_order", "api_parameters"):
        if progress.get(field) != expected[field]:
            raise ValueError(f"{path}: {field} differs from frozen automated audit")
    return progress


def record_failure(
    progress_path: Path,
    progress: dict[str, Any],
    review_id: str,
    role: str,
    error: Exception,
) -> None:
    progress["failures"].append(
        {"review_id": review_id, "role": role, "error": str(error)}
    )
    save_progress(progress_path, progress)


def run_automated_audit(
    packet_path: Path,
    key_path: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    packet = bmr.load_packet(packet_path)
    key = bmr.load_json(key_path)
    if key.get("packet_hash") != packet["packet_hash"]:
        raise ValueError("unblinding key does not match blind packet")
    base_config = benchmark.load_model_config(config_path)
    configs = {
        role: role_config(base_config, role)
        for role in ("judge_a", "judge_b", "arbitrator")
    }
    progress_path = output_dir / PROGRESS_FILE
    progress = load_or_create_progress(progress_path, packet, configs)
    units = {unit["review_id"]: unit for unit in packet["review_units"]}
    total = len(progress["review_order"])

    for position, review_id in enumerate(progress["review_order"], start=1):
        unit = units[review_id]
        unit_state = progress["completed"].setdefault(review_id, {})
        unit_index = int(review_id[1:])
        missing_judges = [
            role for role in ("judge_a", "judge_b") if role not in unit_state
        ]

        def run_judge(role: str) -> tuple[Any, str, int, list[dict[str, str]]]:
            return call_with_one_format_repair(
                configs[role],
                judge_messages(unit, role),
                ROLE_SEED_BASES[role] + unit_index,
                lambda text, current=unit: parse_audit(text, current),
                "只修正输出格式。严格输出一行 AUDIT JSON，包含 C1-C8 和五个固定字段，不解释。",
            )

        if missing_judges:
            for role in missing_judges:
                print(
                    f"AUTOMATED_AUDIT {position}/{total} {review_id} {role}",
                    flush=True,
                )
            judge_results: dict[
                str, tuple[Any, str, int, list[dict[str, str]]]
            ] = {}
            judge_errors: dict[str, Exception] = {}
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(missing_judges)
            ) as executor:
                futures = {
                    executor.submit(run_judge, role): role for role in missing_judges
                }
                for future in concurrent.futures.as_completed(futures):
                    role = futures[future]
                    try:
                        judge_results[role] = future.result()
                    except Exception as error:
                        judge_errors[role] = error
            for role in missing_judges:
                if role not in judge_results:
                    continue
                parsed, raw, calls, deviations = judge_results[role]
                unit_state[role] = {
                    "parsed": parsed,
                    "raw": raw,
                    "model_call_count": calls,
                    "protocol_deviations": deviations,
                }
                save_progress(progress_path, progress)
            if judge_errors:
                for role, error in judge_errors.items():
                    record_failure(progress_path, progress, review_id, role, error)
                failed_roles = ", ".join(sorted(judge_errors))
                raise RuntimeError(
                    f"automated judge call failed for {review_id}: {failed_roles}"
                ) from judge_errors[sorted(judge_errors)[0]]

        disagreements = disagreement_items(
            unit_state["judge_a"]["parsed"], unit_state["judge_b"]["parsed"]
        )
        if "arbitrator" not in unit_state:
            if not disagreements:
                unit_state["arbitrator"] = {
                    "status": "not_needed",
                    "decisions": [],
                    "model_call_count": 0,
                    "protocol_deviations": [],
                }
                save_progress(progress_path, progress)
            else:
                print(
                    f"AUTOMATED_AUDIT {position}/{total} {review_id} arbitrator "
                    f"disagreements={len(disagreements)}",
                    flush=True,
                )
                messages, order = arbitrator_messages(unit, disagreements, unit_index)
                try:
                    decisions, raw, calls, deviations = call_with_one_format_repair(
                        configs["arbitrator"],
                        messages,
                        ROLE_SEED_BASES["arbitrator"] + unit_index,
                        lambda text, current=unit, expected=disagreements: parse_decisions(
                            text, current, expected
                        ),
                        "只修正输出格式。严格输出一行 DECISIONS JSON，恰好覆盖全部输入分歧，不解释。",
                    )
                except Exception as error:
                    record_failure(
                        progress_path, progress, review_id, "arbitrator", error
                    )
                    raise
                unit_state["arbitrator"] = {
                    "status": "completed",
                    "judge_label_order": order,
                    "decisions": decisions,
                    "raw": raw,
                    "model_call_count": calls,
                    "protocol_deviations": deviations,
                }
                save_progress(progress_path, progress)

    return finalize_automated_audit(packet, key, progress, output_dir)


def judgment_rows(
    packet: dict[str, Any], progress: dict[str, Any], role: str, reviewer_id: str
) -> list[dict[str, str]]:
    rows = bmr.review_template_rows(packet, reviewer_id)
    for row in rows:
        parsed = progress["completed"][row["review_id"]][role]["parsed"][
            row["candidate_id"]
        ]
        row.update(parsed)
    return rows


def automated_adjudication_rows(
    packet: dict[str, Any],
    progress: dict[str, Any],
    judge_a_rows: list[dict[str, str]],
    judge_b_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    review_a = {
        (row["review_id"], row["candidate_id"]): row for row in judge_a_rows
    }
    review_b = {
        (row["review_id"], row["candidate_id"]): row for row in judge_b_rows
    }
    rows = bmr.disagreement_rows(
        packet, "api-judge-a", review_a, "api-judge-b", review_b
    )
    decisions = {
        (review_id, item["candidate_id"], item["field"]): item["final_value"]
        for review_id, state in progress["completed"].items()
        for item in state["arbitrator"]["decisions"]
    }
    for row in rows:
        key = (row["review_id"], row["candidate_id"], row["field"])
        row["final_value"] = decisions[key]
        row["adjudicator_id"] = "api-arbitrator"
    return rows


def model_call_summary(progress: dict[str, Any]) -> dict[str, int]:
    calls: Counter[str] = Counter()
    for state in progress["completed"].values():
        for role in ("judge_a", "judge_b", "arbitrator"):
            calls[role] += state[role]["model_call_count"]
    return dict(calls)


def format_repair_summary(progress: dict[str, Any]) -> dict[str, int]:
    repairs: Counter[str] = Counter()
    for state in progress["completed"].values():
        for role in ("judge_a", "judge_b", "arbitrator"):
            repairs[role] += len(state[role]["protocol_deviations"])
    return dict(repairs)


def automated_result_schema(result: dict[str, Any]) -> dict[str, Any]:
    """Remove the legacy human-review labels from the reused sensitivity engine."""
    result["original_auto_vs_automated_consensus"] = result.pop(
        "auto_vs_human_consensus"
    )
    result["original_auto_gates"] = result.pop("auto_gates")
    result["automated_consensus_gates"] = result.pop("human_consensus_gates")
    for comparison in result["condition_comparison"].values():
        comparison["original_auto"] = comparison.pop("auto")
        comparison["automated_consensus"] = comparison.pop("human_consensus")
        comparison["delta_automated_consensus_minus_original_auto"] = comparison.pop(
            "delta_human_minus_auto"
        )
        comparison[
            "original_auto_vs_automated_consensus_mapping_disagreement_count"
        ] = comparison.pop("auto_vs_human_mapping_disagreement_count")
        comparison[
            "original_auto_vs_automated_consensus_mapping_disagreement_rate"
        ] = comparison.pop("auto_vs_human_mapping_disagreement_rate")
        comparison["automated_consensus_quality_rates"] = comparison.pop(
            "human_quality_rates"
        )
    for item in result["gate_flips"]:
        item["original_auto"] = item.pop("auto")
        item["automated_consensus"] = item.pop("human_consensus")
    return result


def automated_report(result: dict[str, Any]) -> str:
    agreement = result["reviewer_agreement"]["mapped_evidence_id"]
    auto_consensus = result["original_auto_vs_automated_consensus"]
    decision = result["decision"]
    lines = [
        "# Candidate Generation v0.4：全自动盲映射敏感性报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        "## 决策结论",
        "",
        f"- 建议：`{decision['recommendation']}`",
        f"- 两个自动评审的映射完全一致率：`{agreement['exact_agreement_rate']:.3f}`；Cohen's kappa：`{agreement['cohen_kappa']:.3f}`。",
        f"- 原自动匹配与仲裁共识不一致率：`{auto_consensus['mapping_disagreement_rate']:.3f}`（{auto_consensus['mapping_disagreement_count']}/{auto_consensus['mapping_rows']}）。",
        f"- 预注册推进门槛翻转：`{len(result['gate_flips'])}` 项。",
        f"- 模型调用：`{json.dumps(result['automated_audit']['model_call_count'], ensure_ascii=False)}`。",
        f"- 其中纯格式修复：`{json.dumps(result['automated_audit']['format_repair_count'], ensure_ascii=False)}`；占首次判断调用的 `{result['automated_audit']['format_repair_rate']:.3f}`。",
        "",
        "## 分条件结果",
        "",
        "| 条件 | 自动全覆盖 | 仲裁全覆盖 | 自动最低覆盖 | 仲裁最低覆盖 | 自动匹配率 | 仲裁匹配率 | 映射差异率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in bmr.GENERATOR_CONDITIONS:
        comparison = result["condition_comparison"][condition]
        auto = comparison["original_auto"]
        consensus = comparison["automated_consensus"]
        lines.append(
            f"| {condition} | {auto['both_branches_full_critical_coverage_count']:.0f}/12 | {consensus['both_branches_full_critical_coverage_count']:.0f}/12 | {auto['minimum_branch_critical_coverage']:.3f} | {consensus['minimum_branch_critical_coverage']:.3f} | {auto['catalog_match_rate']:.3f} | {consensus['catalog_match_rate']:.3f} | {comparison['original_auto_vs_automated_consensus_mapping_disagreement_rate']:.3f} |"
        )
    lines.extend(["", "## 门槛敏感性", ""])
    if result["gate_flips"]:
        lines.extend(
            f"- {item['condition']} / `{item['gate']}`：原自动 `{item['original_auto']}` → 仲裁 `{item['automated_consensus']}`"
            for item in result["gate_flips"]
        )
    else:
        lines.append("- 自动仲裁共识没有改变任何预注册推进门槛判定。")
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            f"- 这是自动化提示独立性审计，不是人工确认；本轮三个角色均使用 `{result['automated_audit']['api_parameters']['judge_a']['model_name']}`，仍可能共享系统性语义偏差。",
            "- 自动评审看不到生成条件、原匹配、隐藏事实和下游结果；仲裁只处理分歧字段，不能修改双方一致项。",
            "- 纯格式修复率较高，说明当前模型/API 的结构化输出稳定性不足；全部修复都没有补充案例信息，且最终没有硬失败。",
            "- 本报告可以决定当前工程路线，但不能把同模型共识称为外部金标准。",
            "",
        ]
    )
    return "\n".join(lines)


def finalize_automated_audit(
    packet: dict[str, Any], key: dict[str, Any], progress: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    if len(progress["completed"]) != packet["review_unit_count"]:
        raise ValueError("automated audit is incomplete")
    judge_a_rows = judgment_rows(packet, progress, "judge_a", "api-judge-a")
    judge_b_rows = judgment_rows(packet, progress, "judge_b", "api-judge-b")
    bmr.write_csv(output_dir / "judge-a.csv", bmr.REVIEW_COLUMNS, judge_a_rows)
    bmr.write_csv(output_dir / "judge-b.csv", bmr.REVIEW_COLUMNS, judge_b_rows)
    adjudication_rows = automated_adjudication_rows(
        packet, progress, judge_a_rows, judge_b_rows
    )
    adjudication_path = output_dir / "adjudication.csv"
    bmr.write_csv(adjudication_path, bmr.ADJUDICATION_COLUMNS, adjudication_rows)
    judge_a_id, review_a = bmr.load_review(output_dir / "judge-a.csv", packet)
    judge_b_id, review_b = bmr.load_review(output_dir / "judge-b.csv", packet)
    consensus = bmr.consensus_review(
        packet,
        judge_a_id,
        review_a,
        judge_b_id,
        review_b,
        adjudication_path,
    )
    result, consensus_rows = bmr.sensitivity_analysis(
        packet,
        key,
        judge_a_id,
        review_a,
        judge_b_id,
        review_b,
        consensus,
    )
    result = automated_result_schema(result)
    for row in consensus_rows:
        row["original_auto_mapping"] = row.pop("auto_mapping")
    same_model = len(
        {
            progress["api_parameters"][role].get("model_name")
            for role in ("judge_a", "judge_b", "arbitrator")
        }
    ) == 1
    if result["decision"]["reviewer_reliability_warning"]:
        recommendation = "refine_automated_judge_protocol_before_gq2"
    elif result["decision"]["material_mapping_issue"]:
        recommendation = "fix_mapping_interface_before_gq2"
    else:
        recommendation = "proceed_to_gq2_generator_development"
    result["decision"]["recommendation"] = recommendation
    model_calls = model_call_summary(progress)
    format_repairs = format_repair_summary(progress)
    primary_calls = {
        role: model_calls[role] - format_repairs[role]
        for role in ("judge_a", "judge_b", "arbitrator")
    }
    total_primary_calls = sum(primary_calls.values())
    total_format_repairs = sum(format_repairs.values())
    result["automated_audit"] = {
        "same_model_for_all_roles": same_model,
        "api_parameters": progress["api_parameters"],
        "primary_judgment_call_count": primary_calls,
        "model_call_count": model_calls,
        "format_repair_count": format_repairs,
        "format_repair_rate": total_format_repairs / total_primary_calls,
        "logged_failure_count": len(progress["failures"]),
        "arbitrated_field_count": len(adjudication_rows),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "automated-sensitivity-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "AUTOMATED-SENSITIVITY-REPORT.md").write_text(
        automated_report(result), encoding="utf-8"
    )
    consensus_columns = (
        "review_id",
        "pair_id",
        "generator",
        "model_seed",
        "candidate_id",
        "original_auto_mapping",
        *bmr.REVIEW_FIELDS,
    )
    bmr.write_csv(
        output_dir / "consensus-mappings.csv", consensus_columns, consensus_rows
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a fully automated condition-blind mapping audit"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    run_parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    finalize_parser.add_argument("--key", type=Path, default=DEFAULT_KEY)
    finalize_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "run":
        result = run_automated_audit(args.packet, args.key, args.config, args.output)
    else:
        packet = bmr.load_packet(args.packet)
        key = bmr.load_json(args.key)
        progress = bmr.load_json(args.output / PROGRESS_FILE)
        result = finalize_automated_audit(packet, key, progress, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
