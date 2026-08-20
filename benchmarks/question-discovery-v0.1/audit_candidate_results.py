from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import benchmark
import candidate_benchmark
import paired_benchmark


ROOT = Path(__file__).resolve().parent
DEFAULT_PROGRESS = [
    ROOT / "results" / f"candidate-v0.4-seed{seed}.json" for seed in (1, 2, 3)
]
CONDITIONS = ("N", "A", "G0", "GQ", "GS", "GB")
GENERATOR_CONDITIONS = ("G0", "GQ", "GS", "GB")


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def assert_close(expected: Any, actual: Any, label: str) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            raise ValueError(f"{label}: keys differ")
        for key in expected:
            assert_close(expected[key], actual[key], f"{label}/{key}")
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            raise ValueError(f"{label}: list lengths differ")
        for index, (left, right) in enumerate(zip(expected, actual)):
            assert_close(left, right, f"{label}[{index}]")
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isclose(float(expected), float(actual), abs_tol=1e-12):
            raise ValueError(f"{label}: {expected} != {actual}")
        return
    if expected != actual:
        raise ValueError(f"{label}: {expected!r} != {actual!r}")


def load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if '"api_key"' in text or "Bearer " in text:
        raise ValueError(f"credential-like field found in {path}")
    return json.loads(text)


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paired_units": len(rows),
        "counterfactual_separation_gain": mean(
            [row["counterfactual_separation_gain"] for row in rows]
        ),
        "post_choice_accuracy": mean([row["post_choice_accuracy"] for row in rows]),
        "both_post_decisions_correct_count": sum(
            row["both_post_decisions_correct"] for row in rows
        ),
        "correct_option_probability_gain": mean(
            [row["correct_option_probability_gain"] for row in rows]
        ),
        "mean_probability_quality_improvement": mean(
            [row["mean_probability_quality_improvement"] for row in rows]
        ),
        "mean_questions_used": mean([row["mean_questions_used"] for row in rows]),
        "mean_no_fact_answer_rate": mean(
            [row["mean_no_fact_answer_rate"] for row in rows]
        ),
        "protocol_deviation_count": sum(
            row["protocol_deviation_count"] for row in rows
        ),
    }


def summarize_candidates(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [artifact["candidate_metrics"] for artifact in artifacts]
    return {
        "candidate_sets": len(artifacts),
        "both_branches_full_critical_coverage_count": sum(
            item["both_branches_full_critical_coverage"] for item in metrics
        ),
        "minimum_branch_critical_coverage": mean(
            [item["minimum_branch_critical_coverage"] for item in metrics]
        ),
        "catalog_match_rate": mean([item["catalog_match_rate"] for item in metrics]),
        "unique_evidence_count": mean(
            [item["unique_evidence_count"] for item in metrics]
        ),
        "duplicate_match_count": mean(
            [item["duplicate_match_count"] for item in metrics]
        ),
        "normalized_menu_count": mean(
            [item["normalized_menu_count"] for item in metrics]
        ),
        "format_repair_count": sum(
            len(artifact["protocol_deviations"]) for artifact in artifacts
        ),
        "generator_model_call_count": sum(
            artifact["generator_model_call_count"] for artifact in artifacts
        ),
        "matcher_model_call_count": sum(
            artifact["matcher_model_call_count"] for artifact in artifacts
        ),
    }


def audit(progress_paths: list[Path]) -> dict[str, Any]:
    pairs = candidate_benchmark.load_pairs()
    expected_keys = {
        f"{pair_id}|{condition}|seed-{seed}"
        for seed in (1, 2, 3)
        for pair_id in pairs
        for condition in CONDITIONS
    }
    seen_keys: set[str] = set()
    result_paths: set[Path] = set()
    session_paths: set[Path] = set()
    candidate_paths: set[Path] = set()
    rows: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    failure_count = 0
    model_names: set[str] = set()
    integrity_errors: list[str] = []

    for progress_path in progress_paths:
        progress = load_json(progress_path)
        if progress.get("mode") != "api" or progress.get("conditions") != list(CONDITIONS):
            raise ValueError(f"{progress_path}: unexpected mode or conditions")
        if len(progress.get("completed", [])) != 24:
            raise ValueError(f"{progress_path}: expected 24 completed runs")
        if progress.get("api_parameters", {}).get("max_tokens") != 1536:
            raise ValueError(f"{progress_path}: max_tokens differs")
        if progress.get("api_parameters", {}).get("thinking_budget") != 512:
            raise ValueError(f"{progress_path}: thinking_budget differs")
        failure_count += len(progress.get("failures", []))

        for completed in progress["completed"]:
            key = completed["run_key"]
            if key in seen_keys:
                raise ValueError(f"duplicate run key: {key}")
            seen_keys.add(key)
            result_path = Path(completed["result_file"]).resolve()
            if result_path in result_paths:
                raise ValueError(f"duplicate result file: {result_path}")
            result_paths.add(result_path)
            result = load_json(result_path)
            condition = completed["condition"]
            pair_id = completed["pair_id"]
            if (
                result["benchmark_version"] != "0.4"
                or result["pair_id"] != pair_id
                or result["condition"] != condition
                or result["model_seed"] != progress["model_seed"]
            ):
                raise ValueError(f"{result_path}: result metadata differs")

            run_cases = pairs[pair_id]
            if condition in GENERATOR_CONDITIONS:
                candidate_path = Path(result["candidate_file"]).resolve()
                if candidate_path in candidate_paths:
                    raise ValueError(f"duplicate candidate artifact: {candidate_path}")
                candidate_paths.add(candidate_path)
                artifact = load_json(candidate_path)
                if (
                    artifact["pair_id"] != pair_id
                    or artifact["generator"] != condition
                    or artifact["model_seed"] != progress["model_seed"]
                ):
                    raise ValueError(f"{candidate_path}: candidate metadata differs")
                if artifact.get("api_parameters") != progress.get("api_parameters"):
                    raise ValueError(f"{candidate_path}: API parameters differ")
                recalculated_candidates = candidate_benchmark.candidate_metrics(
                    run_cases,
                    artifact["candidates"],
                    artifact["matches"],
                    artifact["normalized_menu"],
                )
                assert_close(
                    artifact["candidate_metrics"],
                    recalculated_candidates,
                    str(candidate_path),
                )
                assert_close(
                    result["candidate_metrics"],
                    recalculated_candidates,
                    str(result_path),
                )
                run_cases = candidate_benchmark.derive_cases(
                    run_cases, artifact["normalized_menu"]
                )
                artifact["condition"] = condition
                artifact["model_seed"] = progress["model_seed"]
                artifacts.append(artifact)
            elif result.get("candidate_file") is not None:
                raise ValueError(f"{result_path}: free-question condition has candidate file")

            pair_sessions: list[dict[str, Any]] = []
            if len(result["session_files"]) != 2:
                raise ValueError(f"{result_path}: expected two session files")
            for case, raw_path in zip(run_cases, result["session_files"]):
                session_path = Path(raw_path).resolve()
                if session_path in session_paths:
                    raise ValueError(f"duplicate session file: {session_path}")
                session_paths.add(session_path)
                session = load_json(session_path)
                expected_oracle = (
                    "evidence_catalog"
                    if condition in GENERATOR_CONDITIONS
                    else "semantic_api"
                )
                if (
                    session["benchmark_version"] != "0.4"
                    or session["condition"] != condition
                    or session["model_seed"] != progress["model_seed"]
                    or session["case_id"] != case["case_id"]
                    or session["oracle_mode"] != expected_oracle
                    or session.get("api_parameters") != progress.get("api_parameters")
                ):
                    raise ValueError(f"{session_path}: session metadata differs")
                recalculated_session = benchmark.score_session(case, session)
                assert_close(
                    session["automatic_metrics"], recalculated_session, str(session_path)
                )
                model_names.add(session["model_name"])
                pair_sessions.append(session)
                sessions.append(session)

            recalculated_pair = paired_benchmark.score_pair_sessions(
                run_cases, pair_sessions
            )
            assert_close(result["paired_metrics"], recalculated_pair, str(result_path))
            row = dict(recalculated_pair)
            row["model_seed"] = progress["model_seed"]
            rows.append(row)

    if seen_keys != expected_keys:
        raise ValueError("completed run keys differ from registered 72-unit schedule")
    if len(rows) != 72 or len(sessions) != 144 or len(artifacts) != 48:
        raise ValueError("expected 72 results, 144 sessions, and 48 candidate artifacts")
    if len(model_names) != 1:
        raise ValueError(f"expected one model, found {sorted(model_names)}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    artifact_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_seed: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["condition"]].append(row)
        by_pair[(row["pair_id"], row["condition"])].append(row)
        by_seed[(row["model_seed"], row["condition"])].append(row)
    for artifact in artifacts:
        artifact_groups[artifact["condition"]].append(artifact)

    selector_calls = defaultdict(int)
    semantic_oracle_calls = defaultdict(int)
    session_deviations = defaultdict(int)
    for session in sessions:
        condition = session["condition"]
        selector_calls[condition] += session["model_call_count"]
        session_deviations[condition] += len(session["protocol_deviations"])
        if session["oracle_mode"] == "semantic_api":
            semantic_oracle_calls[condition] += len(session["questions"])

    combined: dict[str, dict[str, Any]] = {}
    for condition in CONDITIONS:
        combined[condition] = summarize_rows(grouped[condition])
        combined[condition].update(
            {
                "selector_model_call_count": selector_calls[condition],
                "semantic_oracle_call_count": semantic_oracle_calls[condition],
                "session_protocol_deviation_count": session_deviations[condition],
            }
        )
        if condition in GENERATOR_CONDITIONS:
            candidate_summary = summarize_candidates(artifact_groups[condition])
            combined[condition]["candidate_generation"] = candidate_summary
            combined[condition]["tested_model_call_count"] = (
                selector_calls[condition]
                + candidate_summary["generator_model_call_count"]
                + candidate_summary["matcher_model_call_count"]
            )
        else:
            combined[condition]["tested_model_call_count"] = selector_calls[condition]

    case_differences: dict[str, dict[str, float]] = {}
    for pair_id in pairs:
        case_differences[pair_id] = {}
        for condition in GENERATOR_CONDITIONS:
            baseline = "N" if condition == "G0" else "G0"
            case_differences[pair_id][f"{condition}-{baseline}"] = mean(
                [row["counterfactual_separation_gain"] for row in by_pair[(pair_id, condition)]]
            ) - mean(
                [row["counterfactual_separation_gain"] for row in by_pair[(pair_id, baseline)]]
            )

    g0 = combined["G0"]
    g0_candidates = g0["candidate_generation"]
    g0_gates = {
        "full_critical_coverage_at_least_8": (
            g0_candidates["both_branches_full_critical_coverage_count"] >= 8
        ),
        "catalog_match_rate_at_least_0_60": (
            g0_candidates["catalog_match_rate"] >= 0.60
        ),
        "unique_evidence_at_least_3_5": (
            g0_candidates["unique_evidence_count"] >= 3.5
        ),
        "main_metric_not_below_N": (
            g0["counterfactual_separation_gain"]
            >= combined["N"]["counterfactual_separation_gain"]
        ),
        "no_case_G0_minus_N_below_minus_0_100": all(
            values["G0-N"] >= -0.100 for values in case_differences.values()
        ),
        "post_accuracy_not_below_N": (
            g0["post_choice_accuracy"] >= combined["N"]["post_choice_accuracy"]
        ),
        "integrity_passed": not integrity_errors,
    }
    gates: dict[str, Any] = {
        "G0": {**g0_gates, "passed": all(g0_gates.values())}
    }
    for condition in ("GQ", "GS", "GB"):
        current = combined[condition]
        current_candidates = current["candidate_generation"]
        if g0_candidates["both_branches_full_critical_coverage_count"] >= 10:
            coverage_gate = (
                current_candidates["both_branches_full_critical_coverage_count"]
                >= g0_candidates["both_branches_full_critical_coverage_count"]
            )
        else:
            coverage_gate = (
                current_candidates["both_branches_full_critical_coverage_count"]
                >= g0_candidates["both_branches_full_critical_coverage_count"] + 2
            )
        condition_gates = {
            "full_critical_coverage_increment": coverage_gate,
            "minimum_critical_coverage_not_below_G0": (
                current_candidates["minimum_branch_critical_coverage"]
                >= g0_candidates["minimum_branch_critical_coverage"]
            ),
            "main_metric_increment_at_least_0_050": (
                current["counterfactual_separation_gain"]
                - g0["counterfactual_separation_gain"]
                >= 0.050
            ),
            "no_case_difference_below_minus_0_100": all(
                values[f"{condition}-G0"] >= -0.100
                for values in case_differences.values()
            ),
            "post_accuracy_not_below_G0": (
                current["post_choice_accuracy"] >= g0["post_choice_accuracy"]
            ),
            "tested_calls_not_above_1_5x_G0": (
                current["tested_model_call_count"]
                <= 1.5 * g0["tested_model_call_count"]
            ),
            "integrity_passed": not integrity_errors,
        }
        gates[condition] = {
            **condition_gates,
            "passed": all(condition_gates.values()),
        }

    return {
        "status": "passed",
        "model_name": next(iter(model_names)),
        "progress_files": len(progress_paths),
        "paired_units": len(rows),
        "sessions": len(sessions),
        "candidate_artifacts": len(artifacts),
        "logged_pre_amendment_failures": failure_count,
        "integrity_errors": integrity_errors,
        "combined": combined,
        "by_seed": {
            f"seed-{seed}/{condition}": summarize_rows(seed_rows)
            for (seed, condition), seed_rows in sorted(by_seed.items())
        },
        "case_main_metric_differences": case_differences,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit candidate benchmark v0.4")
    parser.add_argument("progress", type=Path, nargs="*", default=DEFAULT_PROGRESS)
    args = parser.parse_args()
    paths = [path if path.is_absolute() else ROOT / path for path in args.progress]
    print(json.dumps(audit(paths), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
