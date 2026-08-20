from __future__ import annotations

import argparse
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
DEFAULT_CONFIG = ROOT / "model-config.mode-2.local.json"
DEFAULT_OUTPUT = ROOT / "automated-review-mode-2-v0.4"
PROGRESS_FILE = "mode-2-progress.json"
MODE_ID = "api_mode_2_contract_proof_falsifier_v0.4"
SCHEDULE_SEED = 20260825
ROLE_SEED_BASES = {"extractor": 710000, "prover": 720000, "falsifier": 730000}
REASON_CODES = {
    "ACCEPT",
    "CONTRACT_OMISSION",
    "PARTIAL_COVERAGE",
    "WRONG_ITEM",
    "COMPOSITE_QUESTION",
    "OTHER",
}


EXTRACTOR_PROMPT = """你是 API 模式 2 的问题契约提取器。你看不到证据目录、可用数据字段、原自动映射、生成方法、隐藏事实、正确行动或下游成绩。

对 C1-C8 分别提取“必须同时满足，才算完整回答该候选”的证据要求。要求应明确对象、指标、比较/切分、时间范围和操作；不要猜测有哪些数据可用，不要写 E1-E6 或 NONE。

同时判断：
- atomic_single_observation：是否只要求一个可独立回答的观察/比较/检验；
- distinct_from_other_candidates：是否与同组其他候选在证据与机制上实质独立；
- action_discriminating：至少一种合理答案是否可能改变两个以上公开行动的相对支持。

严格只输出：
CONTRACTS: {"C1":{"requirements":["要求1"],"atomic_single_observation":0或1,"distinct_from_other_candidates":0或1,"action_discriminating":0或1},...,"C8":{...}}"""


PROVER_PROMPT = """你是 API 模式 2 的单项证据覆盖证明器。你只看到已经锁定的问题契约、E1-E6 目录和公开数据能力，看不到候选原文、模式 1、原自动映射、生成方法、隐藏事实、正确行动或下游成绩。

对每个候选，检查是否存在某一个目录项能够单独覆盖全部 requirements。不得假定多个目录项组合、未列字段、额外联表、额外交叉分组或因果识别。

- coverage_by_evidence 必须恰好包含 E1-E6；每个值列出该目录项能够覆盖的、从 1 开始的 requirement 索引。
- 若某一个 E 项覆盖全部要求：mapped_evidence_id 必须是其中一个完整覆盖项。
- 若没有任何 E 项覆盖全部要求：mapped_evidence_id 必须为 NONE。

严格只输出：
PROOFS: {"C1":{"mapped_evidence_id":"E1或NONE","coverage_by_evidence":{"E1":[1],"E2":[],"E3":[],"E4":[],"E5":[],"E6":[]}},...,"C8":{...}}"""


FALSIFIER_PROMPT = """你是 API 模式 2 的反例验证器。你看到候选原文、锁定契约、目录、公开数据能力和覆盖证明，但看不到模式 1、原自动映射、生成方法、隐藏事实、正确行动或下游成绩。

逐题尝试推翻上游链路：
1. 契约是否遗漏候选要求的对象、指标、比较、切分、时间或操作；
2. 覆盖证明是否擅自假定联表、额外字段、多个目录项组合或因果识别；
3. 候选是否原子、与同组候选实质独立、可能改变行动相对支持。

若 contract_faithful=1 且 proof_valid=1，final_mapped_evidence_id 必须等于 Prover 的映射且 reason_code=ACCEPT。若任一为 0，最终映射必须为 NONE，reason_code 必须指出主要问题。reason_code 只能是 ACCEPT、CONTRACT_OMISSION、PARTIAL_COVERAGE、WRONG_ITEM、COMPOSITE_QUESTION、OTHER。你不能未经新证明改映射到另一个 E 项。

严格只输出：
VERDICTS: {"C1":{"contract_faithful":0或1,"proof_valid":0或1,"final_mapped_evidence_id":"E1或NONE","atomic_single_observation":0或1,"distinct_from_other_candidates":0或1,"action_discriminating":0或1,"reason_code":"ACCEPT等固定代码"},...,"C8":{...}}"""


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


def expected_candidate_ids(unit: dict[str, Any]) -> set[str]:
    return {item["id"] for item in unit["candidates"]}


def parse_contracts(text: str, unit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = extract_marker_json(text, "CONTRACTS")
    expected = expected_candidate_ids(unit)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("CONTRACTS must contain exactly C1-C8")
    parsed: dict[str, dict[str, Any]] = {}
    fields = {
        "requirements",
        "atomic_single_observation",
        "distinct_from_other_candidates",
        "action_discriminating",
    }
    for candidate_id in sorted(expected):
        raw = payload[candidate_id]
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError(f"{candidate_id}: invalid contract fields")
        requirements = raw["requirements"]
        if not isinstance(requirements, list) or not 1 <= len(requirements) <= 8:
            raise ValueError(f"{candidate_id}: requirements must contain 1-8 items")
        cleaned: list[str] = []
        for requirement in requirements:
            if not isinstance(requirement, str) or not 3 <= len(requirement.strip()) <= 300:
                raise ValueError(f"{candidate_id}: invalid requirement text")
            value = requirement.strip()
            if re.search(r"\b(?:E[1-6]|NONE)\b", value, flags=re.IGNORECASE):
                raise ValueError(f"{candidate_id}: contract leaked catalog identifiers")
            cleaned.append(value)
        parsed[candidate_id] = {"requirements": cleaned}
        for field in fields - {"requirements"}:
            parsed[candidate_id][field] = normalize_binary(
                raw[field], f"{candidate_id}/{field}"
            )
    return parsed


def normalized_indexes(value: Any, label: str) -> list[int]:
    if not isinstance(value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) for item in value
    ):
        raise ValueError(f"{label} must be an integer list")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicates")
    return sorted(value)


def parse_proofs(
    text: str,
    unit: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payload = extract_marker_json(text, "PROOFS")
    expected = expected_candidate_ids(unit)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("PROOFS must contain exactly C1-C8")
    evidence_ids = {item["id"] for item in unit["evidence_catalog"]}
    fields = {
        "mapped_evidence_id",
        "coverage_by_evidence",
    }
    parsed: dict[str, dict[str, Any]] = {}
    for candidate_id in sorted(expected):
        raw = payload[candidate_id]
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError(f"{candidate_id}: invalid proof fields")
        mapping = str(raw["mapped_evidence_id"]).strip().upper()
        if mapping not in evidence_ids | {"NONE"}:
            raise ValueError(f"{candidate_id}: invalid mapping {mapping!r}")
        required = set(range(1, len(contracts[candidate_id]["requirements"]) + 1))
        coverage_raw = raw["coverage_by_evidence"]
        if not isinstance(coverage_raw, dict) or set(coverage_raw) != evidence_ids:
            raise ValueError(
                f"{candidate_id}: coverage_by_evidence must contain every E item"
            )
        coverage: dict[str, list[int]] = {}
        for evidence_id in sorted(evidence_ids):
            indexes = normalized_indexes(
                coverage_raw[evidence_id], f"{candidate_id}/{evidence_id}"
            )
            if not set(indexes) <= required:
                raise ValueError(f"{candidate_id}: coverage index out of range")
            coverage[evidence_id] = indexes
        fully_covering = {
            evidence_id
            for evidence_id, indexes in coverage.items()
            if set(indexes) == required
        }
        if mapping == "NONE" and fully_covering:
            raise ValueError(f"{candidate_id}: NONE conflicts with a full coverage proof")
        if mapping != "NONE" and mapping not in fully_covering:
            raise ValueError(f"{candidate_id}: E mapping must cover every requirement")
        parsed[candidate_id] = {
            "mapped_evidence_id": mapping,
            "coverage_by_evidence": coverage,
        }
    return parsed


def parse_verdicts(
    text: str,
    unit: dict[str, Any],
    proofs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    payload = extract_marker_json(text, "VERDICTS")
    expected = expected_candidate_ids(unit)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("VERDICTS must contain exactly C1-C8")
    evidence_ids = {item["id"] for item in unit["evidence_catalog"]}
    fields = {
        "contract_faithful",
        "proof_valid",
        "final_mapped_evidence_id",
        "atomic_single_observation",
        "distinct_from_other_candidates",
        "action_discriminating",
        "reason_code",
    }
    parsed: dict[str, dict[str, str]] = {}
    binary_fields = fields - {"final_mapped_evidence_id", "reason_code"}
    for candidate_id in sorted(expected):
        raw = payload[candidate_id]
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError(f"{candidate_id}: invalid verdict fields")
        row = {
            field: normalize_binary(raw[field], f"{candidate_id}/{field}")
            for field in binary_fields
        }
        mapping = str(raw["final_mapped_evidence_id"]).strip().upper()
        if mapping not in evidence_ids | {"NONE"}:
            raise ValueError(f"{candidate_id}: invalid final mapping")
        reason = str(raw["reason_code"]).strip().upper()
        if reason not in REASON_CODES:
            raise ValueError(f"{candidate_id}: invalid reason_code")
        upstream_valid = row["contract_faithful"] == "1" and row["proof_valid"] == "1"
        proposed = proofs[candidate_id]["mapped_evidence_id"]
        if upstream_valid and (mapping != proposed or reason != "ACCEPT"):
            raise ValueError(f"{candidate_id}: accepted verdict must preserve proof")
        if not upstream_valid and (mapping != "NONE" or reason == "ACCEPT"):
            raise ValueError(f"{candidate_id}: rejected verdict must map NONE")
        parsed[candidate_id] = {
            **row,
            "final_mapped_evidence_id": mapping,
            "reason_code": reason,
        }
    return parsed


def mode_2_role_config(base: dict[str, Any], role: str) -> dict[str, Any]:
    if base.get("api_audit_mode") != 2:
        raise ValueError("模式 2 配置缺少 api_audit_mode=2 身份标记")
    role_prefix = f"{role}_"
    config: dict[str, Any] = {}
    for field in ("url", "api_key", "model_name"):
        value = base.get(role_prefix + field)
        if value in (None, ""):
            value = base.get(field)
        if field in {"url", "model_name"} and not value:
            raise ValueError(
                f"模式 2 缺少独立配置：{field} 或 {role_prefix + field}"
            )
        if value is not None:
            config[field] = value
    for field in ("timeout_seconds", "api_max_retries", "send_seed"):
        if base.get(field) is not None:
            config[field] = base[field]
    config["temperature"] = 0
    config["max_tokens"] = int(base.get("max_tokens", 3072))
    enable_thinking = bool(base.get("enable_thinking", False))
    config["enable_thinking"] = enable_thinking
    if enable_thinking:
        config["thinking_budget"] = int(
            base.get("thinking_budget", 512)
        )
    return config


def safe_role_config(config: dict[str, Any]) -> dict[str, Any]:
    return benchmark.api_runtime_parameters(config)


def public_case_without_capabilities(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in unit["public_case"].items()
        if key != "evidence_capabilities"
    }


def extractor_messages(unit: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "review_id": unit["review_id"],
        "public_case": public_case_without_capabilities(unit),
        "candidates": unit["candidates"],
    }
    return [
        {"role": "system", "content": EXTRACTOR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def prover_messages(
    unit: dict[str, Any], contracts: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    payload = {
        "review_id": unit["review_id"],
        "evidence_catalog": unit["evidence_catalog"],
        "evidence_capabilities": unit["public_case"].get("evidence_capabilities", []),
        "contracts": contracts,
    }
    return [
        {"role": "system", "content": PROVER_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def falsifier_messages(
    unit: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
    proofs: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "review_id": unit["review_id"],
        "public_case": unit["public_case"],
        "evidence_catalog": unit["evidence_catalog"],
        "candidates": unit["candidates"],
        "contracts": contracts,
        "proofs": proofs,
    }
    return [
        {"role": "system", "content": FALSIFIER_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


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
        return parsed, repaired, 2, [
            {"type": "format_repair", "error": str(first_error), "raw": raw}
        ]


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
        "mode_id": MODE_ID,
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
    path: Path,
    packet: dict[str, Any],
    configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected = initial_progress(packet, configs)
    if not path.exists():
        save_progress(path, expected)
        return expected
    progress = bmr.load_json(path)
    for field in (
        "mode_id",
        "packet_hash",
        "schedule_seed",
        "review_order",
        "api_parameters",
    ):
        if progress.get(field) != expected[field]:
            raise ValueError(f"{path}: {field} differs from frozen API mode 2")
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


def run_mode_2_api(
    packet_path: Path, config_path: Path, output_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    packet = bmr.load_packet(packet_path)
    base_config = benchmark.load_model_config(config_path)
    configs = {
        role: mode_2_role_config(base_config, role)
        for role in ("extractor", "prover", "falsifier")
    }
    progress_path = output_dir / PROGRESS_FILE
    progress = load_or_create_progress(progress_path, packet, configs)
    units = {unit["review_id"]: unit for unit in packet["review_units"]}
    total = len(progress["review_order"])

    for position, review_id in enumerate(progress["review_order"], start=1):
        unit = units[review_id]
        state = progress["completed"].setdefault(review_id, {})
        unit_index = int(review_id[1:])
        if "extractor" not in state:
            print(f"API_MODE_2 {position}/{total} {review_id} extractor", flush=True)
            try:
                parsed, raw, calls, deviations = call_with_one_format_repair(
                    configs["extractor"],
                    extractor_messages(unit),
                    ROLE_SEED_BASES["extractor"] + unit_index,
                    lambda text, current=unit: parse_contracts(text, current),
                    "只修正格式；严格输出一行 CONTRACTS JSON，包含 C1-C8 和固定字段。",
                )
            except Exception as error:
                record_failure(progress_path, progress, review_id, "extractor", error)
                raise
            state["extractor"] = {
                "parsed": parsed,
                "raw": raw,
                "model_call_count": calls,
                "protocol_deviations": deviations,
            }
            save_progress(progress_path, progress)

        contracts = state["extractor"]["parsed"]
        if "prover" not in state:
            print(f"API_MODE_2 {position}/{total} {review_id} prover", flush=True)
            try:
                parsed, raw, calls, deviations = call_with_one_format_repair(
                    configs["prover"],
                    prover_messages(unit, contracts),
                    ROLE_SEED_BASES["prover"] + unit_index,
                    lambda text, current=unit, locked=contracts: parse_proofs(
                        text, current, locked
                    ),
                    "只修正格式；严格输出一行 PROOFS JSON，每题必须包含 E1-E6 的完整覆盖矩阵。",
                )
            except Exception as error:
                record_failure(progress_path, progress, review_id, "prover", error)
                raise
            state["prover"] = {
                "parsed": parsed,
                "raw": raw,
                "model_call_count": calls,
                "protocol_deviations": deviations,
            }
            save_progress(progress_path, progress)

        proofs = state["prover"]["parsed"]
        if "falsifier" not in state:
            print(f"API_MODE_2 {position}/{total} {review_id} falsifier", flush=True)
            try:
                parsed, raw, calls, deviations = call_with_one_format_repair(
                    configs["falsifier"],
                    falsifier_messages(unit, contracts, proofs),
                    ROLE_SEED_BASES["falsifier"] + unit_index,
                    lambda text, current=unit, locked=proofs: parse_verdicts(
                        text, current, locked
                    ),
                    "只修正格式；严格输出一行 VERDICTS JSON，接受时保留证明映射，否决时映射 NONE。",
                )
            except Exception as error:
                record_failure(progress_path, progress, review_id, "falsifier", error)
                raise
            state["falsifier"] = {
                "parsed": parsed,
                "raw": raw,
                "model_call_count": calls,
                "protocol_deviations": deviations,
            }
            save_progress(progress_path, progress)
    return packet, progress


def final_review(
    packet: dict[str, Any], progress: dict[str, Any]
) -> dict[tuple[str, str], dict[str, str]]:
    review: dict[tuple[str, str], dict[str, str]] = {}
    for unit in packet["review_units"]:
        review_id = unit["review_id"]
        verdicts = progress["completed"][review_id]["falsifier"]["parsed"]
        for candidate in unit["candidates"]:
            candidate_id = candidate["id"]
            verdict = verdicts[candidate_id]
            mapping = verdict["final_mapped_evidence_id"]
            review[(review_id, candidate_id)] = {
                "mapped_evidence_id": mapping,
                "atomic_single_observation": verdict["atomic_single_observation"],
                "fully_answerable_by_mapping": "0" if mapping == "NONE" else "1",
                "distinct_from_other_candidates": verdict[
                    "distinct_from_other_candidates"
                ],
                "action_discriminating": verdict["action_discriminating"],
            }
    return review


def neutralize_result_schema(result: dict[str, Any]) -> dict[str, Any]:
    result.pop("reviewers", None)
    result.pop("reviewer_agreement", None)
    result["original_auto_vs_mode_2_consensus"] = result.pop(
        "auto_vs_human_consensus"
    )
    result["original_auto_gates"] = result.pop("auto_gates")
    result["mode_2_consensus_gates"] = result.pop("human_consensus_gates")
    for comparison in result["condition_comparison"].values():
        comparison["original_auto"] = comparison.pop("auto")
        comparison["mode_2_consensus"] = comparison.pop("human_consensus")
        comparison["delta_mode_2_minus_original_auto"] = comparison.pop(
            "delta_human_minus_auto"
        )
        comparison["original_auto_vs_mode_2_mapping_disagreement_count"] = (
            comparison.pop("auto_vs_human_mapping_disagreement_count")
        )
        comparison["original_auto_vs_mode_2_mapping_disagreement_rate"] = (
            comparison.pop("auto_vs_human_mapping_disagreement_rate")
        )
        comparison["mode_2_quality_rates"] = comparison.pop("human_quality_rates")
    for item in result["gate_flips"]:
        item["original_auto"] = item.pop("auto")
        item["mode_2_consensus"] = item.pop("human_consensus")
    result["decision"].pop("reviewer_reliability_warning", None)
    return result


def pipeline_summary(progress: dict[str, Any]) -> dict[str, Any]:
    calls: Counter[str] = Counter()
    repairs: Counter[str] = Counter()
    contract_rejections = 0
    proof_rejections = 0
    mapping_vetoes = 0
    for state in progress["completed"].values():
        for role in ("extractor", "prover", "falsifier"):
            calls[role] += state[role]["model_call_count"]
            repairs[role] += len(state[role]["protocol_deviations"])
        proofs = state["prover"]["parsed"]
        verdicts = state["falsifier"]["parsed"]
        for candidate_id, verdict in verdicts.items():
            contract_rejections += verdict["contract_faithful"] == "0"
            proof_rejections += verdict["proof_valid"] == "0"
            mapping_vetoes += (
                verdict["final_mapped_evidence_id"]
                != proofs[candidate_id]["mapped_evidence_id"]
            )
    primary = {role: calls[role] - repairs[role] for role in ROLE_SEED_BASES}
    total_primary = sum(primary.values())
    total_repairs = sum(repairs.values())
    models = {
        progress["api_parameters"][role].get("model_name")
        for role in ROLE_SEED_BASES
    }
    return {
        "api_parameters": progress["api_parameters"],
        "same_model_within_mode_2": len(models) == 1,
        "primary_call_count": dict(primary),
        "model_call_count": dict(calls),
        "format_repair_count": dict(repairs),
        "format_repair_rate": total_repairs / total_primary if total_primary else 0,
        "contract_rejection_count": contract_rejections,
        "proof_rejection_count": proof_rejections,
        "mapping_veto_count": mapping_vetoes,
        "logged_failure_count": len(progress["failures"]),
    }


def mode_2_report(result: dict[str, Any]) -> str:
    comparison = result["original_auto_vs_mode_2_consensus"]
    pipeline = result["mode_2_pipeline"]
    lines = [
        "# Candidate Generation v0.4：API 模式 2 独立审计报告",
        "",
        f"Packet hash: `{result['packet_hash']}`",
        "",
        "## 决策结论",
        "",
        f"- 建议：`{result['decision']['recommendation']}`",
        f"- 原自动匹配与模式 2 共识不一致率：`{comparison['mapping_disagreement_rate']:.3f}`（{comparison['mapping_disagreement_count']}/{comparison['mapping_rows']}）。",
        f"- 预注册推进门槛翻转：`{len(result['gate_flips'])}` 项。",
        f"- 契约否决：`{pipeline['contract_rejection_count']}`；证明否决：`{pipeline['proof_rejection_count']}`；映射否决：`{pipeline['mapping_veto_count']}`。",
        f"- 格式修复率：`{pipeline['format_repair_rate']:.3f}`。",
        "",
        "## 分条件结果",
        "",
        "| 条件 | 原自动全覆盖 | 模式 2 全覆盖 | 原自动匹配率 | 模式 2 匹配率 | 映射差异率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in bmr.GENERATOR_CONDITIONS:
        row = result["condition_comparison"][condition]
        original = row["original_auto"]
        mode_2 = row["mode_2_consensus"]
        lines.append(
            f"| {condition} | {original['both_branches_full_critical_coverage_count']:.0f}/12 | {mode_2['both_branches_full_critical_coverage_count']:.0f}/12 | {original['catalog_match_rate']:.3f} | {mode_2['catalog_match_rate']:.3f} | {row['original_auto_vs_mode_2_mapping_disagreement_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 隔离与结论边界",
            "",
            "- 模式 2 的 API 阶段没有读取模式 1 的进度、评分、仲裁或报告，也没有接收原自动映射和下游结果。",
            "- 这是契约—证明—反例链路的自动稳定性检查，不是人工或外部金标准。",
            "- 本报告不计算模式 1/2 一致率；两边锁定后只能使用独立只读比较器。",
            "",
        ]
    )
    return "\n".join(lines)


def finalize_mode_2(
    packet_path: Path,
    key_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    packet = bmr.load_packet(packet_path)
    key = bmr.load_json(key_path)
    progress = bmr.load_json(output_dir / PROGRESS_FILE)
    if progress.get("mode_id") != MODE_ID:
        raise ValueError("progress is not API mode 2")
    if progress.get("packet_hash") != packet["packet_hash"]:
        raise ValueError("progress packet hash differs")
    if len(progress.get("completed", {})) != packet["review_unit_count"]:
        raise ValueError("API mode 2 is incomplete")
    for review_id, state in progress["completed"].items():
        if set(state) != {"extractor", "prover", "falsifier"}:
            raise ValueError(f"{review_id}: API mode 2 stages are incomplete")

    review = final_review(packet, progress)
    result, consensus_rows = bmr.sensitivity_analysis(
        packet,
        key,
        "api-mode-2-locked-copy-a",
        review,
        "api-mode-2-locked-copy-b",
        review,
        review,
    )
    result = neutralize_result_schema(result)
    material = result["decision"]["material_mapping_issue"]
    result["decision"]["recommendation"] = (
        "fix_mapping_interface_before_gq2"
        if material
        else "proceed_to_gq2_generator_development"
    )
    result["mode_id"] = MODE_ID
    result["mode_2_pipeline"] = pipeline_summary(progress)
    for row in consensus_rows:
        row["original_auto_mapping"] = row.pop("auto_mapping")

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = bmr.review_template_rows(packet, "api-mode-2")
    for row in rows:
        row.update(review[(row["review_id"], row["candidate_id"])])
    bmr.write_csv(output_dir / "mode-2-final.csv", bmr.REVIEW_COLUMNS, rows)
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
    stage_files = {
        "contracts.json": "extractor",
        "coverage-proofs.json": "prover",
        "falsifier-verdicts.json": "falsifier",
    }
    for filename, role in stage_files.items():
        payload = {
            review_id: state[role]["parsed"]
            for review_id, state in sorted(progress["completed"].items())
        }
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (output_dir / "mode-2-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "MODE-2-REPORT.md").write_text(
        mode_2_report(result), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated API audit mode 2")
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
        run_mode_2_api(args.packet, args.config, args.output)
    result = finalize_mode_2(args.packet, args.key, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
