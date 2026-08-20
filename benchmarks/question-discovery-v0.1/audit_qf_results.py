from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import benchmark
import paired_benchmark


ROOT = Path(__file__).resolve().parent
DEFAULT_PROGRESS = [
    ROOT / "results" / f"qf-development-progress-v0.3-seed{seed}.json"
    for seed in (1, 2, 3)
]


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def assert_close_mapping(
    expected: dict[str, Any], actual: dict[str, Any], label: str
) -> None:
    if expected.keys() != actual.keys():
        raise ValueError(f"{label}: metric keys differ")
    for key in expected:
        left, right = expected[key], actual[key]
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if not math.isclose(float(left), float(right), abs_tol=1e-12):
                raise ValueError(f"{label}/{key}: {left} != {right}")
        elif left != right:
            raise ValueError(f"{label}/{key}: {left!r} != {right!r}")


def validate_catalog_state(session: dict[str, Any]) -> None:
    questions = session["questions"]
    evidence_ids = [item.get("evidence_id") for item in questions]
    if any(not item for item in evidence_ids):
        raise ValueError(f"{session['case_id']}: missing evidence_id")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError(f"{session['case_id']}: reused evidence_id")
    if any(item.get("fact_id") == "NONE" for item in questions):
        raise ValueError(f"{session['case_id']}: catalog selection returned NONE")
    attempted_evidence = session["evidence_state"]["attempted_evidence_ids"]
    if len(attempted_evidence) != len(evidence_ids) or set(attempted_evidence) != set(
        evidence_ids
    ):
        raise ValueError(f"{session['case_id']}: evidence state differs from questions")

    if session["condition"] == "Q":
        return

    plan = session["explanation_state"]["plan"]
    if len(plan) != 3:
        raise ValueError(f"{session['case_id']}: F plan must contain three entries")
    for field in ("id", "evidence_id", "action"):
        values = [item[field] for item in plan]
        if len(values) != len(set(values)):
            raise ValueError(f"{session['case_id']}: F plan reuses {field}")
    plan_by_id = {item["id"]: item for item in plan}
    targets = [item.get("explanation_target") for item in questions]
    if len(targets) != len(set(targets)):
        raise ValueError(f"{session['case_id']}: F reused TARGET")
    attempted = session["explanation_state"]["attempted_targets"]
    if list(attempted) != targets:
        raise ValueError(f"{session['case_id']}: TARGET state differs from questions")
    for question in questions:
        target = question["explanation_target"]
        if target not in plan_by_id:
            raise ValueError(f"{session['case_id']}: unknown TARGET {target}")
        if plan_by_id[target]["evidence_id"] != question["evidence_id"]:
            raise ValueError(f"{session['case_id']}: TARGET evidence binding changed")
        if attempted[target] != question["fact_id"]:
            raise ValueError(f"{session['case_id']}: TARGET result state changed")


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paired_units": len(rows),
        "counterfactual_separation_gain": mean(
            [row["counterfactual_separation_gain"] for row in rows]
        ),
        "post_choice_accuracy": mean([row["post_choice_accuracy"] for row in rows]),
        "both_post_decisions_correct": sum(
            row["both_post_decisions_correct"] for row in rows
        ),
        "correct_option_probability_gain": mean(
            [row["correct_option_probability_gain"] for row in rows]
        ),
        "mean_probability_quality_improvement": mean(
            [row["mean_probability_quality_improvement"] for row in rows]
        ),
        "mean_questions_used": mean([row["mean_questions_used"] for row in rows]),
        "protocol_deviation_count": sum(
            row["protocol_deviation_count"] for row in rows
        ),
        "first_selection_critical_sessions": sum(
            row["mean_first_selection_critical"] * 2 for row in rows
        ),
        "mean_supporting_evidence_selection_rate": mean(
            [row["mean_supporting_evidence_selection_rate"] for row in rows]
        ),
        "mean_distractor_evidence_selection_rate": mean(
            [row["mean_distractor_evidence_selection_rate"] for row in rows]
        ),
    }


def audit(progress_paths: list[Path]) -> dict[str, Any]:
    pairs = paired_benchmark.load_pairs()
    cases = {
        case["case_id"]: case for variants in pairs.values() for case in variants
    }
    result_paths: set[Path] = set()
    session_paths: set[Path] = set()
    rows: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    failure_count = 0
    model_names: set[str] = set()

    for progress_path in progress_paths:
        progress_text = progress_path.read_text(encoding="utf-8")
        if '"api_key"' in progress_text or "Bearer " in progress_text:
            raise ValueError(f"credential-like field found in {progress_path}")
        progress = json.loads(progress_text)
        if progress.get("mode") != "api" or progress.get("conditions") != ["Q", "F"]:
            raise ValueError(f"{progress_path}: unexpected mode or conditions")
        if len(progress.get("completed", [])) != 8:
            raise ValueError(f"{progress_path}: expected 8 completed runs")
        run_keys = [item["run_key"] for item in progress["completed"]]
        if len(run_keys) != len(set(run_keys)):
            raise ValueError(f"{progress_path}: duplicate run key")
        failure_count += len(progress.get("failures", []))

        for completed in progress["completed"]:
            result_path = Path(completed["result_file"]).resolve()
            if result_path in result_paths:
                raise ValueError(f"duplicate result file: {result_path}")
            result_paths.add(result_path)
            result_text = result_path.read_text(encoding="utf-8")
            if '"api_key"' in result_text or "Bearer " in result_text:
                raise ValueError(f"credential-like field found in {result_path}")
            result = json.loads(result_text)
            if result["pair_id"] != completed["pair_id"]:
                raise ValueError(f"{result_path}: pair id differs from progress")
            if result["condition"] != completed["condition"]:
                raise ValueError(f"{result_path}: condition differs from progress")

            pair_sessions: list[dict[str, Any]] = []
            for raw_path in result["session_files"]:
                session_path = Path(raw_path).resolve()
                if session_path in session_paths:
                    raise ValueError(f"duplicate session file: {session_path}")
                session_paths.add(session_path)
                raw_text = session_path.read_text(encoding="utf-8")
                if '"api_key"' in raw_text or "Bearer " in raw_text:
                    raise ValueError(f"credential-like field found in {session_path}")
                session = json.loads(raw_text)
                if session["model_seed"] != progress["model_seed"]:
                    raise ValueError(f"{session_path}: model seed differs from progress")
                if session["condition"] != result["condition"]:
                    raise ValueError(f"{session_path}: condition differs from result")
                if session["mode"] != "api" or session["oracle_mode"] != "evidence_catalog":
                    raise ValueError(f"{session_path}: unexpected execution mode")
                validate_catalog_state(session)
                recalculated = benchmark.score_session(cases[session["case_id"]], session)
                assert_close_mapping(
                    session["automatic_metrics"], recalculated, str(session_path)
                )
                model_names.add(session["model_name"])
                pair_sessions.append(session)
                sessions.append(session)

            recalculated_pair = paired_benchmark.score_pair_sessions(
                pairs[result["pair_id"]], pair_sessions
            )
            assert_close_mapping(
                result["paired_metrics"], recalculated_pair, str(result_path)
            )
            row = dict(result["paired_metrics"])
            row["model_seed"] = progress["model_seed"]
            rows.append(row)

    if len(rows) != 24 or len(sessions) != 48:
        raise ValueError("expected 24 paired units and 48 sessions")
    if len(model_names) != 1:
        raise ValueError(f"expected one model, found {sorted(model_names)}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_seed: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["condition"]].append(row)
        by_seed[(row["model_seed"], row["condition"])].append(row)
        by_pair[(row["pair_id"], row["condition"])].append(row)

    calls = defaultdict(int)
    questions = defaultdict(int)
    deviations = defaultdict(int)
    for session in sessions:
        condition = session["condition"]
        calls[condition] += session["model_call_count"]
        questions[condition] += len(session["questions"])
        deviations[condition] += len(session["protocol_deviations"])

    all_qf_sessions = {
        path.resolve()
        for path in (ROOT / "sessions").glob("*.json")
        if "-Q-api-" in path.name or "-F-api-" in path.name
    }
    orphan_sessions = sorted(str(path) for path in all_qf_sessions - session_paths)

    return {
        "status": "passed",
        "model_name": next(iter(model_names)),
        "progress_files": len(progress_paths),
        "paired_units": len(rows),
        "sessions": len(sessions),
        "logged_infrastructure_failures": failure_count,
        "unreferenced_qf_sessions": orphan_sessions,
        "combined": {
            condition: {
                **summarize_rows(condition_rows),
                "model_call_count": calls[condition],
                "question_count": questions[condition],
                "session_protocol_deviation_count": deviations[condition],
            }
            for condition, condition_rows in sorted(grouped.items())
        },
        "by_seed": {
            f"seed-{seed}/{condition}": summarize_rows(seed_rows)
            for (seed, condition), seed_rows in sorted(by_seed.items())
        },
        "by_pair": {
            f"{pair_id}/{condition}": summarize_rows(pair_rows)
            for (pair_id, condition), pair_rows in sorted(by_pair.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Q/F development results")
    parser.add_argument("progress", type=Path, nargs="*", default=DEFAULT_PROGRESS)
    args = parser.parse_args()
    paths = [path if path.is_absolute() else ROOT / path for path in args.progress]
    print(json.dumps(audit(paths), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
