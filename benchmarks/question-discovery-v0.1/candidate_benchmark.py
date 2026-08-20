from __future__ import annotations

import argparse
import copy
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import benchmark
import paired_benchmark


ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "cases-v0.4"
RESULTS_DIR = ROOT / "results"
GENERATOR_FILES = {
    "G0": "generator-native.md",
    "GQ": "generator-qft.md",
    "GS": "generator-storm.md",
    "GB": "generator-steelman.md",
}
FREE_QUESTION_FILES = {"N": "native.md", "A": "baseline.md"}
RUN_CONDITIONS = tuple(FREE_QUESTION_FILES) + tuple(GENERATOR_FILES)
MATCHER_PROMPT = "candidate-matcher.md"
SELECTOR_PROMPT = benchmark.EVIDENCE_MENU_PROMPT
CANDIDATE_IDS = [f"C{index}" for index in range(1, 9)]


def load_pairs() -> dict[str, list[dict[str, Any]]]:
    pairs: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(CASES_DIR.glob("*.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        shared = {key: value for key, value in source.items() if key != "variants"}
        variants: list[dict[str, Any]] = []
        for variant in source["variants"]:
            case = copy.deepcopy(shared)
            case.update(copy.deepcopy(variant))
            case["case_id"] = f"{source['pair_id']}--{variant['variant_id']}"
            case["public_case_id"] = source["pair_id"]
            case["_path"] = str(path)
            variants.append(case)
        if source["pair_id"] in pairs:
            raise ValueError(f"duplicate pair_id: {source['pair_id']}")
        pairs[source["pair_id"]] = variants
    return pairs


def public_generator_case(case: dict[str, Any]) -> dict[str, Any]:
    payload = benchmark.public_case(case)
    payload.pop("question_budget", None)
    payload["selection_budget"] = 4
    payload["evidence_capabilities"] = case["evidence_capabilities"]
    return payload


def validate_pairs(pairs: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    domains: list[str] = []
    best_positions: list[str] = []
    if len(pairs) != 4:
        errors.append(f"expected 4 counterfactual pairs, found {len(pairs)}")
    for pair_id, variants in pairs.items():
        if len(variants) != 2:
            errors.append(f"{pair_id}: expected exactly 2 variants")
            continue
        domains.append(variants[0]["domain"])
        public_payloads = [public_generator_case(case) for case in variants]
        if public_payloads[0] != public_payloads[1]:
            errors.append(f"{pair_id}: public generator payload differs between variants")
        catalogs = [case.get("evidence_catalog") for case in variants]
        if catalogs[0] != catalogs[1]:
            errors.append(f"{pair_id}: matcher catalog differs between variants")
        for case in variants:
            errors.extend(
                f"{case['case_id']}: {error}" for error in benchmark.validate_case(case)
            )
            catalog = case.get("evidence_catalog")
            capabilities = case.get("evidence_capabilities")
            if not isinstance(catalog, list) or len(catalog) != 6:
                errors.append(f"{case['case_id']}: evidence_catalog must contain 6 items")
                continue
            if not isinstance(capabilities, list) or len(capabilities) < 3:
                errors.append(
                    f"{case['case_id']}: evidence_capabilities must contain at least 3 sources"
                )
            catalog_ids = [item.get("id") for item in catalog]
            if catalog_ids != [f"E{index}" for index in range(1, 7)]:
                errors.append(f"{case['case_id']}: catalog ids must be E1-E6")
            fact_ids = [fact.get("evidence_id") for fact in case["oracle_facts"]]
            if sorted(fact_ids) != sorted(catalog_ids):
                errors.append(
                    f"{case['case_id']}: each catalog item must map to one fact"
                )
            rendered_public = benchmark.normalize(
                json.dumps(public_payloads[0], ensure_ascii=False)
            )
            rendered_catalog = benchmark.normalize(
                json.dumps(catalog, ensure_ascii=False)
            )
            for term in case.get("leakage_terms", []):
                normalized = benchmark.normalize(term)
                if normalized in rendered_public or normalized in rendered_catalog:
                    errors.append(f"{case['case_id']}: public interface leaks {term!r}")
        best = [benchmark.best_public_option(case) for case in variants]
        if len(set(best)) != 2:
            errors.append(f"{pair_id}: variants must have different best actions")
        best_positions.extend(best)
    if sorted(domains) != ["operations", "product", "project", "research"]:
        errors.append(f"expected one pair per domain, found {sorted(domains)}")
    counts = {option: best_positions.count(option) for option in sorted(set(best_positions))}
    if counts != {"option_a": 2, "option_b": 2, "option_c": 2, "option_d": 2}:
        errors.append(f"best positions are not balanced: {counts}")
    return errors


def parse_candidates(text: str) -> list[dict[str, str]]:
    raw = benchmark.parse_protocol_field(text, "CANDIDATES")
    if raw is None:
        raise ValueError("missing CANDIDATES")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("CANDIDATES must be a JSON array") from error
    if not isinstance(payload, list) or len(payload) != 8:
        raise ValueError("CANDIDATES must contain exactly 8 items")
    candidates: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each candidate must be an object")
        candidate_id = item.get("id")
        question = item.get("question")
        if not isinstance(candidate_id, str) or not isinstance(question, str):
            raise ValueError("candidate id and question must be strings")
        if not question.strip():
            raise ValueError(f"{candidate_id}: question is empty")
        candidates.append({"id": candidate_id, "question": question.strip()})
    if [item["id"] for item in candidates] != CANDIDATE_IDS:
        raise ValueError("candidate ids must appear once in order C1-C8")
    return candidates


def parse_matches(
    text: str, candidates: list[dict[str, str]], catalog_ids: set[str]
) -> dict[str, str]:
    raw = benchmark.parse_protocol_field(text, "MATCHES")
    if raw is None:
        raise ValueError("missing MATCHES")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("MATCHES must be a JSON object") from error
    expected = {item["id"] for item in candidates}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("MATCHES must contain every candidate id exactly once")
    valid = catalog_ids | {"NONE"}
    if any(value not in valid for value in payload.values()):
        raise ValueError(f"MATCHES values must be one of {sorted(valid)}")
    return {item["id"]: payload[item["id"]] for item in candidates}


def call_with_format_repair(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    model_seed: int,
    parser: Any,
    repair_instruction: str,
) -> tuple[Any, str, list[dict[str, str]], int]:
    raw = benchmark.api_chat_completion(config, messages, model_seed)
    try:
        return parser(raw), raw, [], 1
    except ValueError as first_error:
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair_instruction},
        ]
        repaired = benchmark.api_chat_completion(config, repair_messages, model_seed)
        parsed = parser(repaired)
        return (
            parsed,
            repaired,
            [{"type": "format_repair", "error": str(first_error), "raw": raw}],
            2,
        )


def generator_messages(case: dict[str, Any], generator: str) -> list[dict[str, str]]:
    prompt = (benchmark.PROMPTS_DIR / GENERATOR_FILES[generator]).read_text(
        encoding="utf-8"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "## 公开案例\n"
            + json.dumps(public_generator_case(case), ensure_ascii=False, indent=2),
        },
    ]


def matcher_messages(
    case: dict[str, Any], candidates: list[dict[str, str]]
) -> list[dict[str, str]]:
    prompt = (benchmark.PROMPTS_DIR / MATCHER_PROMPT).read_text(encoding="utf-8")
    payload = {
        "evidence_catalog": case["evidence_catalog"],
        "candidates": candidates,
    }
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def generate_and_match(
    case: dict[str, Any],
    generator: str,
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int,
) -> tuple[list[dict[str, str]], dict[str, str], dict[str, Any]]:
    deviations: list[dict[str, str]] = []
    model_calls = 0
    matcher_calls = 0
    if mode == "api":
        if config is None:
            raise ValueError("API mode requires model config")
        candidates, raw_generator, generator_deviations, model_calls = call_with_format_repair(
            config,
            generator_messages(case, generator),
            model_seed,
            parse_candidates,
            "只修正格式。输出一行 CANDIDATES，包含 C1-C8 八个 JSON 对象。",
        )
        deviations.extend(generator_deviations)
        catalog_ids = {item["id"] for item in case["evidence_catalog"]}
        matcher_config = benchmark.oracle_api_config(config)
        matches, raw_matcher, matcher_deviations, matcher_calls = call_with_format_repair(
            matcher_config,
            matcher_messages(case, candidates),
            model_seed,
            lambda text: parse_matches(text, candidates, catalog_ids),
            "只修正格式。输出一行 MATCHES，包含 C1-C8，值只能是 E1-E6 或 NONE。",
        )
        deviations.extend(matcher_deviations)
    else:
        print((benchmark.PROMPTS_DIR / GENERATOR_FILES[generator]).read_text(encoding="utf-8"))
        print(json.dumps(public_generator_case(case), ensure_ascii=False, indent=2))
        raw_generator = input("CANDIDATES 完整一行> ").strip()
        candidates = parse_candidates(raw_generator)
        print((benchmark.PROMPTS_DIR / MATCHER_PROMPT).read_text(encoding="utf-8"))
        print(
            json.dumps(
                {"evidence_catalog": case["evidence_catalog"], "candidates": candidates},
                ensure_ascii=False,
                indent=2,
            )
        )
        raw_matcher = input("MATCHES 完整一行> ").strip()
        matches = parse_matches(
            raw_matcher,
            candidates,
            {item["id"] for item in case["evidence_catalog"]},
        )
    metadata = {
        "generator_raw": raw_generator,
        "matcher_raw": raw_matcher,
        "protocol_deviations": deviations,
        "generator_model_call_count": model_calls,
        "matcher_model_call_count": matcher_calls,
        "api_parameters": (
            benchmark.api_runtime_parameters(config) if config is not None else None
        ),
    }
    return candidates, matches, metadata


def normalize_menu(
    candidates: list[dict[str, str]], matches: dict[str, str]
) -> list[dict[str, str]]:
    menu: list[dict[str, str]] = []
    used_evidence: set[str] = set()
    for candidate in candidates:
        evidence_id = matches[candidate["id"]]
        if evidence_id == "NONE" or evidence_id in used_evidence:
            continue
        used_evidence.add(evidence_id)
        menu.append({**candidate, "base_evidence_id": evidence_id})
    return menu


def derive_cases(
    variants: list[dict[str, Any]], menu: list[dict[str, str]]
) -> list[dict[str, Any]]:
    derived: list[dict[str, Any]] = []
    for original in variants:
        case = copy.deepcopy(original)
        case["evidence_catalog"] = [
            {"id": item["id"], "question": item["question"]} for item in menu
        ]
        fact_by_evidence = {
            fact["evidence_id"]: fact for fact in original["oracle_facts"]
        }
        facts = []
        for item in menu:
            fact = copy.deepcopy(fact_by_evidence[item["base_evidence_id"]])
            fact["evidence_id"] = item["id"]
            fact["source_evidence_id"] = item["base_evidence_id"]
            facts.append(fact)
        case["oracle_facts"] = facts
        case["question_budget"] = min(4, len(menu))
        derived.append(case)
    return derived


def candidate_metrics(
    variants: list[dict[str, Any]],
    candidates: list[dict[str, str]],
    matches: dict[str, str],
    menu: list[dict[str, str]],
) -> dict[str, Any]:
    matched = [value for value in matches.values() if value != "NONE"]
    unique = {item["base_evidence_id"] for item in menu}
    branch_coverages = []
    for case in variants:
        critical = {
            fact["evidence_id"]
            for fact in case["oracle_facts"]
            if fact["criticality"] == "critical"
        }
        branch_coverages.append(len(unique & critical) / len(critical))
    return {
        "candidate_count": len(candidates),
        "catalog_match_rate": len(matched) / len(candidates),
        "unique_evidence_count": len(unique),
        "unique_evidence_coverage": len(unique) / len(variants[0]["evidence_catalog"]),
        "duplicate_match_count": len(matched) - len(unique),
        "normalized_menu_count": len(menu),
        "branch_critical_coverage": branch_coverages,
        "minimum_branch_critical_coverage": min(branch_coverages),
        "both_branches_full_critical_coverage": float(min(branch_coverages) == 1.0),
    }


def save_candidate_artifact(
    pair_id: str,
    generator: str,
    model_seed: int,
    mode: str,
    candidates: list[dict[str, str]],
    matches: dict[str, str],
    menu: list[dict[str, str]],
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> Path:
    now = datetime.now(timezone.utc)
    artifact = {
        "benchmark_version": "0.4",
        "created_at_utc": now.isoformat(),
        "pair_id": pair_id,
        "generator": generator,
        "model_seed": model_seed,
        "mode": mode,
        "candidates": candidates,
        "matches": matches,
        "normalized_menu": menu,
        "candidate_metrics": metrics,
        **metadata,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / (
        f"{pair_id}-{generator}-candidates-seed{model_seed}-"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_result(
    pair_id: str,
    condition: str,
    model_seed: int,
    session_paths: list[Path],
    paired_metrics: dict[str, Any],
    candidate_path: Path | None = None,
    metrics: dict[str, Any] | None = None,
) -> Path:
    now = datetime.now(timezone.utc)
    result = {
        "benchmark_version": "0.4",
        "created_at_utc": now.isoformat(),
        "pair_id": pair_id,
        "condition": condition,
        "model_seed": model_seed,
        "candidate_file": str(candidate_path) if candidate_path else None,
        "candidate_metrics": metrics,
        "session_files": [str(path) for path in session_paths],
        "paired_metrics": paired_metrics,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / (
        f"{pair_id}-{condition}-v0.4-paired-seed{model_seed}-"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_metrics": metrics, "paired_metrics": paired_metrics}, ensure_ascii=False, indent=2))
    print(f"Result saved: {path}")
    return path


def run_sessions(
    variants: list[dict[str, Any]],
    condition: str,
    prompt_file: str,
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int,
) -> tuple[list[Path], list[dict[str, Any]]]:
    paths: list[Path] = []
    for case in variants:
        if mode == "api":
            if config is None:
                raise ValueError("API mode requires model config")
            path = benchmark.run_api_session(
                case,
                condition,
                config,
                model_seed,
                prompt_file=prompt_file,
                benchmark_version="0.4",
            )
        else:
            path = benchmark.run_direct_session(
                case,
                condition,
                prompt_file=prompt_file,
                benchmark_version="0.4",
            )
        paths.append(path)
    sessions = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    return paths, sessions


def run_pair(
    variants: list[dict[str, Any]],
    condition: str,
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int,
) -> Path:
    candidate_path: Path | None = None
    metrics: dict[str, Any] | None = None
    run_variants = variants
    prompt_file = FREE_QUESTION_FILES.get(condition, SELECTOR_PROMPT)
    if condition in GENERATOR_FILES:
        candidates, matches, metadata = generate_and_match(
            variants[0], condition, mode, config, model_seed
        )
        menu = normalize_menu(candidates, matches)
        metrics = candidate_metrics(variants, candidates, matches, menu)
        candidate_path = save_candidate_artifact(
            variants[0]["pair_id"],
            condition,
            model_seed,
            mode,
            candidates,
            matches,
            menu,
            metrics,
            metadata,
        )
        run_variants = derive_cases(variants, menu)
    paths, sessions = run_sessions(
        run_variants, condition, prompt_file, mode, config, model_seed
    )
    paired_metrics = paired_benchmark.score_pair_sessions(run_variants, sessions)
    return save_result(
        variants[0]["pair_id"],
        condition,
        model_seed,
        paths,
        paired_metrics,
        candidate_path,
        metrics,
    )


def build_schedule(
    pairs: dict[str, list[dict[str, Any]]], seed: int, repeats: int
) -> list[dict[str, Any]]:
    runs = [
        {"pair_id": pair_id, "condition": condition, "model_seed": repeat + 1}
        for repeat in range(repeats)
        for pair_id in pairs
        for condition in RUN_CONDITIONS
    ]
    random.Random(seed).shuffle(runs)
    for index, run in enumerate(runs, start=1):
        run["run_id"] = f"GEN-{index:03d}"
    return runs


def build_registered_schedule(
    pairs: dict[str, list[dict[str, Any]]], first_seed: int, repeats: int
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for repeat in range(repeats):
        randomization_seed = first_seed + repeat
        for run in build_schedule(pairs, randomization_seed, 1):
            runs.append(
                {
                    **run,
                    "model_seed": repeat + 1,
                    "randomization_seed": randomization_seed,
                }
            )
    for index, run in enumerate(runs, start=1):
        run["run_id"] = f"GEN-{index:03d}"
    return runs


def run_calibration(
    pairs: dict[str, list[dict[str, Any]]],
    conditions: list[str],
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int,
    randomization_seed: int,
    progress_path: Path,
    max_pair_runs: int | None,
) -> Path:
    api_parameters = (
        benchmark.api_runtime_parameters(config) if config is not None else None
    )
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if (
            progress.get("model_seed") != model_seed
            or progress.get("mode") != mode
            or progress.get("conditions") != conditions
            or progress.get("randomization_seed") != randomization_seed
        ):
            raise ValueError(
                "progress mode/model_seed/conditions/randomization_seed differs; "
                "use a new progress file"
            )
        recorded_parameters = progress.get("api_parameters")
        if progress.get("completed") and recorded_parameters != api_parameters:
            raise ValueError(
                "progress API runtime parameters differ after completed runs; "
                "use a new progress file"
            )
        progress["api_parameters"] = api_parameters
    else:
        progress = {
            "benchmark_version": "0.4",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "model_name": config["model_name"] if config else "direct",
            "model_seed": model_seed,
            "randomization_seed": randomization_seed,
            "conditions": conditions,
            "api_parameters": api_parameters,
            "completed": [],
            "failures": [],
        }
    completed = {item["run_key"] for item in progress["completed"]}
    eligible = [
        run
        for run in build_schedule(pairs, randomization_seed, 1)
        if run["condition"] in conditions
    ]
    executed = 0
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    for run in eligible:
        key = f"{run['pair_id']}|{run['condition']}|seed-{model_seed}"
        if key in completed:
            print(f"SKIP_COMPLETED: {key}")
            continue
        if max_pair_runs is not None and executed >= max_pair_runs:
            break
        print(f"GENERATOR_RUN: {key}")
        try:
            result_path = run_pair(
                pairs[run["pair_id"]], run["condition"], mode, config, model_seed
            )
        except Exception as error:
            progress["failures"].append(
                {
                    "run_key": key,
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
            progress["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            progress_path.write_text(
                json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise
        progress["completed"].append(
            {
                "run_key": key,
                "pair_id": run["pair_id"],
                "condition": run["condition"],
                "model_seed": model_seed,
                "result_file": str(result_path),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        completed.add(key)
        executed += 1
        progress["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    remaining = sum(
        f"{run['pair_id']}|{run['condition']}|seed-{model_seed}" not in completed
        for run in eligible
    )
    print(f"PROGRESS: completed={len(progress['completed'])}, remaining={remaining}")
    return progress_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate question generator benchmark v0.4")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--seed", type=int, default=20260901)
    schedule.add_argument("--repeats", type=int, default=3)
    schedule.add_argument("--output", type=Path)
    run = subparsers.add_parser("run-pair")
    run.add_argument("pair_id")
    run.add_argument("--condition", choices=RUN_CONDITIONS, required=True)
    run.add_argument("--mode", choices=benchmark.RUN_MODES)
    run.add_argument("--config", type=Path, default=benchmark.DEFAULT_CONFIG_PATH)
    run.add_argument("--model-seed", type=int, default=1)
    run.add_argument("--api-max-tokens", type=int)
    run.add_argument("--api-thinking-budget", type=int)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--mode", choices=benchmark.RUN_MODES)
    calibrate.add_argument("--config", type=Path, default=benchmark.DEFAULT_CONFIG_PATH)
    calibrate.add_argument("--model-seed", type=int, default=1)
    calibrate.add_argument("--api-max-tokens", type=int)
    calibrate.add_argument("--api-thinking-budget", type=int)
    calibrate.add_argument("--randomization-seed", type=int, default=20260901)
    calibrate.add_argument(
        "--conditions", choices=RUN_CONDITIONS, nargs="+", default=list(RUN_CONDITIONS)
    )
    calibrate.add_argument("--max-pair-runs", type=int)
    calibrate.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()

    pairs = load_pairs()
    errors = validate_pairs(pairs)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.command == "validate":
        print("Validation passed: 4 new pairs, 8 variants, balanced best actions.")
        return 0
    if args.command == "list":
        for pair_id, variants in pairs.items():
            print(
                f"{pair_id}\t{variants[0]['domain']}\t"
                f"best={','.join(benchmark.best_public_option(case) for case in variants)}"
            )
        return 0
    if args.command == "schedule":
        if args.repeats < 1:
            parser.error("--repeats must be positive")
        payload = {
            "benchmark_version": "0.4",
            "randomization_seeds": [args.seed + index for index in range(args.repeats)],
            "repeats": args.repeats,
            "conditions": list(RUN_CONDITIONS),
            "total_pair_runs": len(pairs) * len(RUN_CONDITIONS) * args.repeats,
            "total_model_sessions": len(pairs) * len(RUN_CONDITIONS) * args.repeats * 2,
            "generator_artifacts": len(pairs) * len(GENERATOR_FILES) * args.repeats,
            "runs": build_registered_schedule(pairs, args.seed, args.repeats),
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            output = args.output if args.output.is_absolute() else ROOT / args.output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Schedule saved: {output}")
        else:
            print(rendered)
        return 0
    if args.command == "run-pair":
        if args.pair_id not in pairs:
            parser.error(f"unknown pair_id: {args.pair_id}")
        mode = args.mode or benchmark.choose_run_mode()
        config = benchmark.load_model_config(args.config) if mode == "api" else None
        if config is not None:
            if args.api_max_tokens is not None:
                if args.api_max_tokens < 1:
                    parser.error("--api-max-tokens must be positive")
                config["max_tokens"] = args.api_max_tokens
            if args.api_thinking_budget is not None:
                if args.api_thinking_budget < 1:
                    parser.error("--api-thinking-budget must be positive")
                config["thinking_budget"] = args.api_thinking_budget
        run_pair(pairs[args.pair_id], args.condition, mode, config, args.model_seed)
        return 0
    if args.command == "calibrate":
        if args.max_pair_runs is not None and args.max_pair_runs < 1:
            parser.error("--max-pair-runs must be positive")
        mode = args.mode or benchmark.choose_run_mode()
        config = benchmark.load_model_config(args.config) if mode == "api" else None
        if config is not None:
            if args.api_max_tokens is not None:
                if args.api_max_tokens < 1:
                    parser.error("--api-max-tokens must be positive")
                config["max_tokens"] = args.api_max_tokens
            if args.api_thinking_budget is not None:
                if args.api_thinking_budget < 1:
                    parser.error("--api-thinking-budget must be positive")
                config["thinking_budget"] = args.api_thinking_budget
        progress_path = args.progress if args.progress.is_absolute() else ROOT / args.progress
        run_calibration(
            pairs,
            list(dict.fromkeys(args.conditions)),
            mode,
            config,
            args.model_seed,
            args.randomization_seed,
            progress_path,
            args.max_pair_runs,
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
