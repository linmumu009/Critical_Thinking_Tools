from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import benchmark


ROOT = Path(__file__).resolve().parent
PAIRS_DIR = ROOT / "cases-v0.3"
RESULTS_DIR = ROOT / "results"
PAIRED_CONDITION_FILES = {"N": "native.md", **benchmark.CONDITION_FILES}


def load_pairs() -> dict[str, list[dict[str, Any]]]:
    pairs: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(PAIRS_DIR.glob("*.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        variants: list[dict[str, Any]] = []
        shared = {
            key: value
            for key, value in source.items()
            if key not in {"variants"}
        }
        for variant in source["variants"]:
            case = dict(shared)
            case.update(variant)
            case["case_id"] = f"{source['pair_id']}--{variant['variant_id']}"
            case["public_case_id"] = source["pair_id"]
            case["_path"] = str(path)
            variants.append(case)
        pairs[source["pair_id"]] = variants
    return pairs


def validate_pairs(pairs: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    if len(pairs) != 4:
        errors.append(f"expected 4 counterfactual pairs, found {len(pairs)}")
    domains: list[str] = []
    best_positions: list[str] = []
    for pair_id, variants in pairs.items():
        if len(variants) != 2:
            errors.append(f"{pair_id}: expected exactly 2 variants")
            continue
        domains.append(variants[0]["domain"])
        for case in variants:
            errors.extend(
                f"{case['case_id']}: {error}"
                for error in benchmark.validate_case(case)
            )
        public_payloads = [benchmark.public_case(case) for case in variants]
        if public_payloads[0] != public_payloads[1]:
            errors.append(f"{pair_id}: public payload differs between variants")
        best = [benchmark.best_public_option(case) for case in variants]
        if len(set(best)) != 2:
            errors.append(f"{pair_id}: variants must have different best actions")
        best_positions.extend(best)
    if sorted(domains) != ["operations", "product", "project", "research"]:
        errors.append(f"expected one pair per domain, found {sorted(domains)}")
    counts = {option: best_positions.count(option) for option in sorted(set(best_positions))}
    if counts != {"option_a": 2, "option_b": 2, "option_c": 2, "option_d": 2}:
        errors.append(f"best positions are not balanced across variants: {counts}")
    return errors


def preflight_messages(case: dict[str, Any], condition: str) -> list[dict[str, str]]:
    prompt = (benchmark.PROMPTS_DIR / PAIRED_CONDITION_FILES[condition]).read_text(
        encoding="utf-8"
    )
    options = ", ".join(benchmark.public_option_map(case))
    return [
        {
            "role": "system",
            "content": (
                prompt
                + "\n\n这是天花板预检。不得提问；只按用户要求给出暂定选择和完整概率。"
            ),
        },
        {
            "role": "user",
            "content": (
                "## 公开案例\n"
                + json.dumps(benchmark.public_case(case), ensure_ascii=False, indent=2)
                + "\n\n现在只输出两行：\n"
                + f"PRE_DECISION: <option_id>（有效选项：{options}）\n"
                + "PRE_PROBABILITIES: <JSON 对象；包含全部选项，概率之和为 1>"
            ),
        },
    ]


def run_preflight(
    pairs: dict[str, list[dict[str, Any]]],
    config: dict[str, Any] | None,
    model_seed: int | None,
    mode: str,
) -> Path:
    rows: list[dict[str, Any]] = []
    for pair_id, variants in sorted(pairs.items()):
        case = variants[0]
        for condition in PAIRED_CONDITION_FILES:
            if mode == "api":
                if config is None:
                    raise ValueError("API mode requires model config")
                raw = benchmark.api_chat_completion(
                    config, preflight_messages(case, condition), model_seed
                )
                choice = benchmark.parse_protocol_field(raw, "PRE_DECISION")
                probabilities = benchmark.parse_probability_field(
                    case, raw, "PRE_PROBABILITIES"
                )
                if choice is None or probabilities is None:
                    raise ValueError(
                        f"{pair_id}/{condition} 预检协议无效：{raw[:300]}"
                    )
                choice = benchmark.validate_option(case, choice, "PRE_DECISION")
            else:
                print(
                    json.dumps(
                        benchmark.public_case(case), ensure_ascii=False, indent=2
                    )
                )
                print(f"条件：{condition}")
                choice = benchmark.choose_option(case, "PRE_DECISION option_id> ")
                probabilities = benchmark.choose_probabilities(
                    case, 'PRE_PROBABILITIES JSON（例如 {"option_a":0.25,...}）> '
                )
            best = [benchmark.best_public_option(variant) for variant in variants]
            correct = sum(choice == option for option in best)
            branch_probability_mass = sum(probabilities[option] for option in set(best))
            branch_probability_gap = abs(
                probabilities[best[0]] - probabilities[best[1]]
            )
            rows.append(
                {
                    "pair_id": pair_id,
                    "condition": condition,
                    "pre_decision": choice,
                    "pre_probabilities": probabilities,
                    "variant_best_options": best,
                    "correct_variants": correct,
                    "variant_count": len(variants),
                    "branch_probability_mass": branch_probability_mass,
                    "branch_probability_gap": branch_probability_gap,
                }
            )
            print(
                f"{pair_id}/{condition}: {choice}; "
                f"best={best}; correct={correct}/{len(variants)}"
            )
    result = {
        "benchmark_version": "0.3-preflight",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model_name": config["model_name"] if config else "direct",
        "model_seed": model_seed,
        "runs": rows,
        "pre_choice_accuracy": (
            sum(row["correct_variants"] for row in rows)
            / sum(row["variant_count"] for row in rows)
        ),
        "mean_branch_probability_mass": sum(
            row["branch_probability_mass"] for row in rows
        )
        / len(rows),
        "mean_branch_probability_gap": sum(
            row["branch_probability_gap"] for row in rows
        )
        / len(rows),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "paired-preflight-v0.3.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Pre-choice accuracy: {result['pre_choice_accuracy']:.3f}")
    print(
        "Mean branch probability gap: "
        f"{result['mean_branch_probability_gap']:.3f}"
    )
    print(f"Preflight saved: {path}")
    return path


def score_pair_sessions(
    variants: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(variants) != 2 or len(sessions) != 2:
        raise ValueError("paired scoring requires exactly two variants and sessions")
    by_variant = {session.get("variant_id"): session for session in sessions}
    ordered = [by_variant.get(case["variant_id"]) for case in variants]
    if any(session is None for session in ordered):
        raise ValueError("session variant ids do not match the counterfactual pair")
    first, second = ordered
    assert first is not None and second is not None
    if first["condition"] != second["condition"]:
        raise ValueError("paired sessions must use the same condition")
    best_first, best_second = [
        benchmark.best_public_option(case) for case in variants
    ]

    def accuracy(field: str) -> float:
        return (
            float(first[field] == best_first) + float(second[field] == best_second)
        ) / 2

    def correct_probability(field: str) -> float:
        return (
            float(first[field][best_first]) + float(second[field][best_second])
        ) / 2

    def separation(field: str) -> float:
        first_probs = first[field]
        second_probs = second[field]
        return 0.5 * (
            (first_probs[best_first] - second_probs[best_first])
            + (second_probs[best_second] - first_probs[best_second])
        )

    pre_accuracy = accuracy("pre_decision")
    post_accuracy = accuracy("post_decision")
    pre_correct_probability = correct_probability("pre_probabilities")
    post_correct_probability = correct_probability("post_probabilities")
    pre_separation = separation("pre_probabilities")
    post_separation = separation("post_probabilities")
    metrics = [session["automatic_metrics"] for session in ordered]
    return {
        "pair_id": variants[0]["pair_id"],
        "condition": first["condition"],
        "variant_best_options": [best_first, best_second],
        "pre_choice_accuracy": pre_accuracy,
        "post_choice_accuracy": post_accuracy,
        "both_post_decisions_correct": float(post_accuracy == 1.0),
        "pre_correct_option_probability": pre_correct_probability,
        "post_correct_option_probability": post_correct_probability,
        "correct_option_probability_gain": (
            post_correct_probability - pre_correct_probability
        ),
        "pre_counterfactual_separation": pre_separation,
        "post_counterfactual_separation": post_separation,
        "counterfactual_separation_gain": post_separation - pre_separation,
        "post_decisions_diverge": float(
            first["post_decision"] != second["post_decision"]
        ),
        "mean_probability_quality_improvement": sum(
            metric.get("probability_quality_improvement", 0.0) for metric in metrics
        )
        / 2,
        "mean_questions_used": sum(metric["questions_used"] for metric in metrics)
        / 2,
        "mean_no_fact_answer_rate": sum(
            metric["no_fact_answer_rate"] for metric in metrics
        )
        / 2,
        "protocol_deviation_count": sum(
            metric["protocol_deviation_count"] for metric in metrics
        ),
    }


def save_pair_result(
    pair_id: str,
    condition: str,
    session_paths: list[Path],
    paired_metrics: dict[str, Any],
) -> Path:
    now = datetime.now(timezone.utc)
    result = {
        "benchmark_version": "0.3",
        "created_at_utc": now.isoformat(),
        "pair_id": pair_id,
        "condition": condition,
        "session_files": [str(path) for path in session_paths],
        "paired_metrics": paired_metrics,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / (
        f"{pair_id}-{condition}-paired-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n成对指标：")
    print(json.dumps(paired_metrics, ensure_ascii=False, indent=2))
    print(f"成对结果已保存：{path}")
    return path


def run_pair(
    variants: list[dict[str, Any]],
    condition: str,
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int | None,
) -> Path:
    session_paths: list[Path] = []
    for case in variants:
        print(f"\n运行隐藏分支：{case['variant_id']}")
        if mode == "api":
            if config is None:
                raise ValueError("API mode requires model config")
            path = benchmark.run_api_session(
                case,
                condition,
                config,
                model_seed,
                prompt_file=PAIRED_CONDITION_FILES[condition],
                benchmark_version="0.3",
            )
        else:
            path = benchmark.run_direct_session(
                case,
                condition,
                prompt_file=PAIRED_CONDITION_FILES[condition],
                benchmark_version="0.3",
            )
        session_paths.append(path)
    sessions = [
        json.loads(path.read_text(encoding="utf-8")) for path in session_paths
    ]
    paired_metrics = score_pair_sessions(variants, sessions)
    return save_pair_result(
        variants[0]["pair_id"], condition, session_paths, paired_metrics
    )


def build_paired_schedule(
    pairs: dict[str, list[dict[str, Any]]], seed: int, repeats: int
) -> list[dict[str, Any]]:
    runs = [
        {
            "pair_id": pair_id,
            "condition": condition,
            "model_seed": repeat + 1,
        }
        for repeat in range(repeats)
        for pair_id in pairs
        for condition in PAIRED_CONDITION_FILES
    ]
    random.Random(seed).shuffle(runs)
    for index, run in enumerate(runs, start=1):
        run["run_id"] = f"PAIR-{index:03d}"
    return runs


def calibration_run_key(pair_id: str, condition: str, model_seed: int) -> str:
    return f"{pair_id}|{condition}|seed-{model_seed}"


def run_calibration(
    pairs: dict[str, list[dict[str, Any]]],
    conditions: list[str],
    excluded_pairs: set[str],
    mode: str,
    config: dict[str, Any] | None,
    model_seed: int,
    randomization_seed: int,
    progress_path: Path,
    max_pair_runs: int | None,
) -> Path:
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("model_seed") != model_seed or progress.get("mode") != mode:
            raise ValueError(
                "进度文件的 mode/model_seed 与当前运行不一致；请使用新进度文件。"
            )
    else:
        progress = {
            "benchmark_version": "0.3",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "model_name": config["model_name"] if config else "direct",
            "model_seed": model_seed,
            "randomization_seed": randomization_seed,
            "excluded_pairs": sorted(excluded_pairs),
            "conditions": conditions,
            "completed": [],
            "failures": [],
        }

    completed_keys = {item["run_key"] for item in progress["completed"]}
    schedule = build_paired_schedule(pairs, randomization_seed, repeats=1)
    eligible = [
        run
        for run in schedule
        if run["pair_id"] not in excluded_pairs
        and run["condition"] in conditions
    ]
    executed = 0
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    for run in eligible:
        key = calibration_run_key(run["pair_id"], run["condition"], model_seed)
        if key in completed_keys:
            print(f"SKIP_COMPLETED: {key}")
            continue
        if max_pair_runs is not None and executed >= max_pair_runs:
            break
        print(f"\nCALIBRATION_RUN: {key}")
        try:
            result_path = run_pair(
                pairs[run["pair_id"]],
                run["condition"],
                mode,
                config,
                model_seed,
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
        completed_keys.add(key)
        executed += 1
        progress["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    remaining = sum(
        calibration_run_key(run["pair_id"], run["condition"], model_seed)
        not in completed_keys
        for run in eligible
    )
    print(
        f"CALIBRATION_PROGRESS: completed={len(progress['completed'])}, "
        f"remaining={remaining}, progress={progress_path}"
    )
    return progress_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Question Discovery v0.3 paired preflight")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    show = subparsers.add_parser("show")
    show.add_argument("pair_id")
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--mode", choices=benchmark.RUN_MODES)
    preflight.add_argument("--config", type=Path, default=benchmark.DEFAULT_CONFIG_PATH)
    preflight.add_argument("--model-seed", type=int, default=11)
    run = subparsers.add_parser("run-pair")
    run.add_argument("pair_id")
    run.add_argument("--condition", choices=PAIRED_CONDITION_FILES, required=True)
    run.add_argument("--mode", choices=benchmark.RUN_MODES)
    run.add_argument("--config", type=Path, default=benchmark.DEFAULT_CONFIG_PATH)
    run.add_argument("--model-seed", type=int, default=1)
    score = subparsers.add_parser("score-pair")
    score.add_argument("session_files", type=Path, nargs=2)
    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--seed", type=int, default=20260819)
    schedule.add_argument("--repeats", type=int, default=1)
    schedule.add_argument("--output", type=Path)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--mode", choices=benchmark.RUN_MODES)
    calibrate.add_argument("--config", type=Path, default=benchmark.DEFAULT_CONFIG_PATH)
    calibrate.add_argument("--model-seed", type=int, default=1)
    calibrate.add_argument("--randomization-seed", type=int, default=20260819)
    calibrate.add_argument(
        "--conditions",
        choices=PAIRED_CONDITION_FILES,
        nargs="+",
        default=list(PAIRED_CONDITION_FILES),
    )
    calibrate.add_argument("--exclude-pair", action="append", default=[])
    calibrate.add_argument("--max-pair-runs", type=int)
    calibrate.add_argument(
        "--progress",
        type=Path,
        default=RESULTS_DIR / "paired-calibration-progress-v0.3.json",
    )
    args = parser.parse_args()

    pairs = load_pairs()
    errors = validate_pairs(pairs)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.command == "validate":
        print("Validation passed: 4 counterfactual pairs, 8 hidden variants.")
        return 0
    if args.command == "list":
        for pair_id, variants in pairs.items():
            best = [benchmark.best_public_option(case) for case in variants]
            print(f"{pair_id}\t{variants[0]['domain']}\tbest={','.join(best)}")
        return 0
    if args.command == "show":
        if args.pair_id not in pairs:
            parser.error(f"unknown pair_id: {args.pair_id}")
        print(
            json.dumps(
                benchmark.public_case(pairs[args.pair_id][0]),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "preflight":
        mode = args.mode or benchmark.choose_run_mode()
        config = benchmark.load_model_config(args.config) if mode == "api" else None
        run_preflight(pairs, config, args.model_seed, mode)
        return 0
    if args.command == "run-pair":
        if args.pair_id not in pairs:
            parser.error(f"unknown pair_id: {args.pair_id}")
        mode = args.mode or benchmark.choose_run_mode()
        config = benchmark.load_model_config(args.config) if mode == "api" else None
        run_pair(
            pairs[args.pair_id],
            args.condition,
            mode,
            config,
            args.model_seed,
        )
        return 0
    if args.command == "score-pair":
        sessions = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in args.session_files
        ]
        pair_id = sessions[0].get("pair_id")
        if pair_id not in pairs or sessions[1].get("pair_id") != pair_id:
            raise ValueError("session files do not belong to the same known pair")
        print(
            json.dumps(
                score_pair_sessions(pairs[pair_id], sessions),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "schedule":
        if args.repeats < 1:
            parser.error("--repeats must be positive")
        result = {
            "benchmark_version": "0.3",
            "randomization_seed": args.seed,
            "repeats": args.repeats,
            "total_pair_runs": len(pairs)
            * len(PAIRED_CONDITION_FILES)
            * args.repeats,
            "total_model_sessions": len(pairs)
            * len(PAIRED_CONDITION_FILES)
            * args.repeats
            * 2,
            "runs": build_paired_schedule(pairs, args.seed, args.repeats),
        }
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            output = args.output if args.output.is_absolute() else ROOT / args.output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Paired schedule saved: {output}")
        else:
            print(rendered)
        return 0
    if args.command == "calibrate":
        unknown_pairs = set(args.exclude_pair) - set(pairs)
        if unknown_pairs:
            parser.error(f"unknown excluded pair(s): {sorted(unknown_pairs)}")
        if args.max_pair_runs is not None and args.max_pair_runs < 1:
            parser.error("--max-pair-runs must be positive")
        mode = args.mode or benchmark.choose_run_mode()
        config = benchmark.load_model_config(args.config) if mode == "api" else None
        progress_path = (
            args.progress if args.progress.is_absolute() else ROOT / args.progress
        )
        run_calibration(
            pairs,
            list(dict.fromkeys(args.conditions)),
            set(args.exclude_pair),
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
