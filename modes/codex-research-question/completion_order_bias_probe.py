"""Audit and summarize completion-order-bias replay experiments.

The probe consumes gradients and effective trajectory weights emitted by an
external replay/training implementation.  It deliberately does not fabricate
model gradients: the bundled demo only exercises the analysis contract.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


SCHEMA_VERSION = "1.0"
CONDITIONS = (
    "real",
    "within_version_random",
    "version_balanced",
    "version_sync_oracle",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _finite_number(value: Any, label: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{label} must be numeric")
    value = float(value)
    _require(math.isfinite(value), f"{label} must be finite")
    return value


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    _require(left_norm > 0 and right_norm > 0, "gradient vectors must have non-zero norm")
    return dot / (left_norm * right_norm)


def _relative_norm_error(value: list[float], oracle: list[float]) -> float:
    error = math.sqrt(sum((a - b) ** 2 for a, b in zip(value, oracle)))
    oracle_norm = math.sqrt(sum(item * item for item in oracle))
    _require(oracle_norm > 0, "oracle gradient must have non-zero norm")
    return error / oracle_norm


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = (cursor + end - 1) / 2.0
        for index in range(cursor, end):
            ranks[ordered[index][0]] = average_rank
        cursor = end
    return ranks


def _spearman(left: list[float], right: list[float]) -> float:
    _require(len(left) == len(right) and len(left) >= 3, "dose response requires at least three levels")
    left_ranks = _rank(left)
    right_ranks = _rank(right)
    left_mean = mean(left_ranks)
    right_mean = mean(right_ranks)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_ranks, right_ranks))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left_ranks)
        * sum((b - right_mean) ** 2 for b in right_ranks)
    )
    _require(denominator > 0, "dose-response ranks must vary")
    return numerator / denominator


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require(payload.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version")
    _require(isinstance(payload.get("study_id"), str) and payload["study_id"], "study_id is required")
    scientific_status = payload.get("scientific_status", "real_replay")
    _require(
        scientific_status in {"real_replay", "synthetic_pipeline_check_only"},
        "scientific_status must be real_replay or synthetic_pipeline_check_only",
    )

    thresholds = payload.get("thresholds")
    _require(isinstance(thresholds, dict), "thresholds must be an object")
    expected_thresholds = {"min_mean_cosine_gap", "require_all_seed_directions", "min_dose_spearman"}
    _require(set(thresholds) == expected_thresholds, "thresholds differ from the preregistered contract")
    min_gap = _finite_number(thresholds["min_mean_cosine_gap"], "min_mean_cosine_gap")
    min_spearman = _finite_number(thresholds["min_dose_spearman"], "min_dose_spearman")
    _require(min_gap >= 0, "min_mean_cosine_gap must be non-negative")
    _require(-1 <= min_spearman <= 1, "min_dose_spearman must be between -1 and 1")
    _require(isinstance(thresholds["require_all_seed_directions"], bool), "require_all_seed_directions must be boolean")

    primary_level = _finite_number(payload.get("primary_injection_level"), "primary_injection_level")
    trajectories = payload.get("trajectories")
    _require(isinstance(trajectories, list) and len(trajectories) >= 4, "at least four trajectories are required")
    if scientific_status == "real_replay":
        _require(256 <= len(trajectories) <= 512, "real replay requires 256-512 trajectories")
    trajectory_by_id: dict[str, dict[str, Any]] = {}
    for index, trajectory in enumerate(trajectories):
        label = f"trajectories[{index}]"
        _require(isinstance(trajectory, dict), f"{label} must be an object")
        expected = {"trajectory_id", "prompt_id", "behavior_version", "completion_time", "duration"}
        _require(set(trajectory) == expected, f"{label} differs from schema")
        trajectory_id = trajectory["trajectory_id"]
        _require(isinstance(trajectory_id, str) and trajectory_id, f"{label}.trajectory_id is required")
        _require(trajectory_id not in trajectory_by_id, f"duplicate trajectory_id: {trajectory_id}")
        _require(isinstance(trajectory["prompt_id"], str) and trajectory["prompt_id"], f"{label}.prompt_id is required")
        _require(isinstance(trajectory["behavior_version"], str) and trajectory["behavior_version"], f"{label}.behavior_version is required")
        _finite_number(trajectory["completion_time"], f"{label}.completion_time")
        duration = _finite_number(trajectory["duration"], f"{label}.duration")
        _require(duration >= 0, f"{label}.duration must be non-negative")
        trajectory_by_id[trajectory_id] = trajectory
    _require(
        len({item["behavior_version"] for item in trajectory_by_id.values()}) >= 2,
        "at least two behavior versions are required",
    )

    runs = payload.get("runs")
    _require(isinstance(runs, list) and runs, "runs must be a non-empty list")
    cells: dict[tuple[int, float, str], dict[str, Any]] = {}
    dimensions: set[int] = set()
    for index, run in enumerate(runs):
        label = f"runs[{index}]"
        _require(isinstance(run, dict), f"{label} must be an object")
        _require(set(run) == {"seed", "injection_level", "condition", "updates"}, f"{label} differs from schema")
        seed = run["seed"]
        _require(isinstance(seed, int) and not isinstance(seed, bool), f"{label}.seed must be an integer")
        level = _finite_number(run["injection_level"], f"{label}.injection_level")
        condition = run["condition"]
        _require(condition in CONDITIONS, f"{label}.condition is invalid")
        key = (seed, level, condition)
        _require(key not in cells, f"duplicate run cell: {key}")
        updates = run["updates"]
        _require(isinstance(updates, list) and updates, f"{label}.updates must be non-empty")
        if scientific_status == "real_replay":
            _require(20 <= len(updates) <= 50, f"{label}.updates must contain 20-50 steps for real replay")
        steps: list[int] = []
        observed_ids: set[str] = set()
        for update_index, update in enumerate(updates):
            update_label = f"{label}.updates[{update_index}]"
            _require(isinstance(update, dict), f"{update_label} must be an object")
            _require(set(update) == {"step", "gradient", "weights"}, f"{update_label} differs from schema")
            step = update["step"]
            _require(isinstance(step, int) and step >= 0, f"{update_label}.step must be a non-negative integer")
            steps.append(step)
            gradient = update["gradient"]
            _require(isinstance(gradient, list) and gradient, f"{update_label}.gradient must be non-empty")
            clean_gradient = [_finite_number(value, f"{update_label}.gradient") for value in gradient]
            dimensions.add(len(clean_gradient))
            weights = update["weights"]
            _require(isinstance(weights, list) and weights, f"{update_label}.weights must be non-empty")
            seen_in_update: set[str] = set()
            for weight_index, weight in enumerate(weights):
                weight_label = f"{update_label}.weights[{weight_index}]"
                _require(isinstance(weight, dict), f"{weight_label} must be an object")
                _require(set(weight) == {"trajectory_id", "weight"}, f"{weight_label} differs from schema")
                trajectory_id = weight["trajectory_id"]
                _require(trajectory_id in trajectory_by_id, f"unknown trajectory_id: {trajectory_id}")
                _require(trajectory_id not in seen_in_update, f"duplicate trajectory weight in update: {trajectory_id}")
                seen_in_update.add(trajectory_id)
                observed_ids.add(trajectory_id)
                _require(_finite_number(weight["weight"], f"{weight_label}.weight") >= 0, f"{weight_label}.weight must be non-negative")
        _require(steps == list(range(len(updates))), f"{label}.steps must be contiguous from zero")
        _require(observed_ids == set(trajectory_by_id), f"{label} must cover the fixed trajectory set")
        cells[key] = run

    _require(len(dimensions) == 1, "all gradients must have the same dimension")
    seeds = sorted({key[0] for key in cells})
    levels = sorted({key[1] for key in cells})
    _require(len(seeds) >= 3, "at least three seeds are required")
    _require(len(levels) >= 3, "at least three injection levels are required")
    _require(primary_level in levels, "primary_injection_level is absent from runs")
    for seed in seeds:
        for level in levels:
            for condition in CONDITIONS:
                _require((seed, level, condition) in cells, f"missing run cell: {(seed, level, condition)}")
            expected_steps = len(cells[(seed, level, "version_sync_oracle")]["updates"])
            for condition in CONDITIONS:
                _require(
                    len(cells[(seed, level, condition)]["updates"]) == expected_steps,
                    f"step-count mismatch for {(seed, level, condition)}",
                )

    return {
        "trajectory_by_id": trajectory_by_id,
        "cells": cells,
        "seeds": seeds,
        "levels": levels,
        "primary_level": primary_level,
        "thresholds": thresholds,
    }


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    state = validate_payload(payload)
    trajectory_by_id = state["trajectory_by_id"]
    slow_count = max(1, math.ceil(len(trajectory_by_id) / 4))
    slow_ids = {
        item["trajectory_id"]
        for item in sorted(
            trajectory_by_id.values(),
            key=lambda item: (-float(item["duration"]), item["trajectory_id"]),
        )[:slow_count]
    }

    summaries: list[dict[str, Any]] = []
    lookup: dict[tuple[int, float, str], dict[str, Any]] = {}
    for seed in state["seeds"]:
        for level in state["levels"]:
            oracle_updates = state["cells"][(seed, level, "version_sync_oracle")]["updates"]
            for condition in CONDITIONS:
                run = state["cells"][(seed, level, condition)]
                cosines: list[float] = []
                norm_errors: list[float] = []
                slow_weight = 0.0
                total_weight = 0.0
                for update, oracle_update in zip(run["updates"], oracle_updates):
                    gradient = [float(value) for value in update["gradient"]]
                    oracle_gradient = [float(value) for value in oracle_update["gradient"]]
                    cosines.append(_cosine(gradient, oracle_gradient))
                    norm_errors.append(_relative_norm_error(gradient, oracle_gradient))
                    for weight in update["weights"]:
                        value = float(weight["weight"])
                        total_weight += value
                        if weight["trajectory_id"] in slow_ids:
                            slow_weight += value
                _require(total_weight > 0, f"total effective weight is zero for {(seed, level, condition)}")
                summary = {
                    "seed": seed,
                    "injection_level": level,
                    "condition": condition,
                    "mean_gradient_cosine": mean(cosines),
                    "mean_relative_norm_error": mean(norm_errors),
                    "slow_quartile_weight_share": slow_weight / total_weight,
                }
                summaries.append(summary)
                lookup[(seed, level, condition)] = summary

    primary_level = state["primary_level"]
    seed_gaps = []
    for seed in state["seeds"]:
        randomized = lookup[(seed, primary_level, "within_version_random")]["mean_gradient_cosine"]
        real = lookup[(seed, primary_level, "real")]["mean_gradient_cosine"]
        seed_gaps.append({"seed": seed, "cosine_gap": randomized - real})

    dose_gaps = []
    for level in state["levels"]:
        gaps = [
            lookup[(seed, level, "within_version_random")]["mean_gradient_cosine"]
            - lookup[(seed, level, "real")]["mean_gradient_cosine"]
            for seed in state["seeds"]
        ]
        dose_gaps.append({"injection_level": level, "mean_cosine_gap": mean(gaps)})

    mean_primary_gap = mean(item["cosine_gap"] for item in seed_gaps)
    direction_consistent = all(item["cosine_gap"] > 0 for item in seed_gaps)
    dose_spearman = _spearman(
        [item["injection_level"] for item in dose_gaps],
        [item["mean_cosine_gap"] for item in dose_gaps],
    )
    thresholds = state["thresholds"]
    gap_pass = mean_primary_gap >= float(thresholds["min_mean_cosine_gap"])
    direction_pass = direction_consistent or not thresholds["require_all_seed_directions"]
    dose_pass = dose_spearman >= float(thresholds["min_dose_spearman"])
    continue_investment = gap_pass and direction_pass and dose_pass
    criterion_outcome = "continue" if continue_investment else "stop_or_narrow"
    scientific_status = payload.get("scientific_status", "real_replay")

    return {
        "schema_version": SCHEMA_VERSION,
        "study_id": payload["study_id"],
        "scientific_status": scientific_status,
        "fixed_set_audit": {
            "passed": True,
            "trajectory_count": len(trajectory_by_id),
            "behavior_version_count": len({item["behavior_version"] for item in trajectory_by_id.values()}),
            "seed_count": len(state["seeds"]),
            "injection_level_count": len(state["levels"]),
            "slow_quartile_trajectory_ids": sorted(slow_ids),
        },
        "cell_summaries": summaries,
        "preregistered_decision": {
            "primary_injection_level": primary_level,
            "mean_cosine_gap_random_minus_real": mean_primary_gap,
            "seed_gaps": seed_gaps,
            "direction_consistent": direction_consistent,
            "dose_gaps": dose_gaps,
            "dose_spearman": dose_spearman,
            "criteria": {
                "mean_gap_pass": gap_pass,
                "seed_direction_pass": direction_pass,
                "dose_response_pass": dose_pass,
            },
            "criterion_outcome": criterion_outcome,
            "outcome": "pipeline_check_only"
            if scientific_status == "synthetic_pipeline_check_only"
            else criterion_outcome,
        },
    }


def make_demo() -> dict[str, Any]:
    trajectories = []
    for index in range(12):
        trajectories.append(
            {
                "trajectory_id": f"t{index:02d}",
                "prompt_id": f"p{index // 3:02d}",
                "behavior_version": f"v{index % 2}",
                "completion_time": float(index),
                "duration": float(index + 1),
            }
        )

    runs = []
    condition_offsets = {
        "real": 0.0,
        "within_version_random": 0.005,
        "version_balanced": 0.003,
        "version_sync_oracle": 0.0,
    }
    for seed in (11, 22, 33):
        generator = random.Random(seed)
        for level in (0.0, 0.5, 1.0):
            for condition in CONDITIONS:
                updates = []
                for step in range(4):
                    oracle_angle = 0.02 * step
                    if condition == "real":
                        angle = oracle_angle + 0.03 + 0.09 * level + generator.uniform(-0.003, 0.003)
                    elif condition == "within_version_random":
                        angle = oracle_angle + condition_offsets[condition] + generator.uniform(-0.001, 0.001)
                    elif condition == "version_balanced":
                        angle = oracle_angle + condition_offsets[condition] + generator.uniform(-0.001, 0.001)
                    else:
                        angle = oracle_angle
                    weights = []
                    for trajectory in trajectories:
                        base_weight = 1.0
                        if condition == "real" and trajectory["trajectory_id"] in {"t09", "t10", "t11"}:
                            base_weight -= 0.25 * level
                        weights.append({"trajectory_id": trajectory["trajectory_id"], "weight": base_weight})
                    updates.append(
                        {
                            "step": step,
                            "gradient": [math.cos(angle), math.sin(angle)],
                            "weights": weights,
                        }
                    )
                runs.append(
                    {
                        "seed": seed,
                        "injection_level": level,
                        "condition": condition,
                        "updates": updates,
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "study_id": "synthetic-contract-dry-run",
        "scientific_status": "synthetic_pipeline_check_only",
        "primary_injection_level": 1.0,
        "thresholds": {
            "min_mean_cosine_gap": 0.05,
            "require_all_seed_directions": True,
            "min_dose_spearman": 0.8,
        },
        "trajectories": trajectories,
        "runs": runs,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a completion-order-bias replay probe")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="validate and analyze a replay payload")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--output", type=Path, required=True)

    demo_parser = subparsers.add_parser("demo", help="write a synthetic contract check and its analysis")
    demo_parser.add_argument("--input-output", type=Path, required=True)
    demo_parser.add_argument("--result-output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "analyze":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = analyze(payload)
        _write_json(args.output, result)
        print(json.dumps(result["preregistered_decision"], ensure_ascii=False, indent=2))
        return 0

    payload = make_demo()
    result = analyze(payload)
    _write_json(args.input_output, payload)
    _write_json(args.result_output, result)
    print(json.dumps(result["preregistered_decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
