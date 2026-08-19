from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import benchmark


ROOT = Path(__file__).resolve().parent
PAIRS_DIR = ROOT / "cases-v0.3"
RESULTS_DIR = ROOT / "results"


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
    prompt = (benchmark.PROMPTS_DIR / benchmark.CONDITION_FILES[condition]).read_text(
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
        for condition in benchmark.CONDITION_FILES:
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
