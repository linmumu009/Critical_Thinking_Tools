from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = ROOT / "research-profile.json"
MODE_ID = "codex_research_question_discovery_v1"
SCORE_DIMENSIONS = (
    "importance",
    "novelty",
    "discriminating_power",
    "feasibility",
    "measurability",
    "expected_information_value",
)
HARD_GATES = (
    "real_gap",
    "non_redundant",
    "falsifiable",
    "feasible",
    "measurable",
    "ethical",
)
CONTRACT_FIELDS = (
    "final_question",
    "hypothesis",
    "counter_hypothesis",
    "independent_variables",
    "dependent_variables",
    "controls",
    "minimum_experiment",
    "falsification_rule",
    "expected_contribution",
    "boundary_conditions",
    "compute_budget_assumption",
    "data_requirements",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("mode_id") != MODE_ID:
        raise ValueError("profile mode_id differs")
    if "研究问题" not in str(profile.get("business_goal", "")):
        raise ValueError("profile must fix research-question discovery as the business")
    focus_areas = profile.get("focus_areas")
    if not isinstance(focus_areas, list) or len(focus_areas) < 3:
        raise ValueError("profile must contain at least three focus areas")
    constraints = profile.get("execution_constraints", {})
    if constraints.get("processor") != "current_codex":
        raise ValueError("mode 2 must be processed by current Codex")
    if constraints.get("external_model_api_allowed") is not False:
        raise ValueError("mode 2 cannot allow an external model API")
    if constraints.get("human_scoring_required") is not False:
        raise ValueError("mode 2 cannot require human scoring")
    if constraints.get("live_primary_source_search_required") is not True:
        raise ValueError("mode 2 must require current primary-source search")


def initial_session(profile: dict[str, Any]) -> dict[str, Any]:
    validate_profile(profile)
    return {
        "schema_version": "1.0",
        "mode_id": MODE_ID,
        "status": "collecting_evidence",
        "created_date": date.today().isoformat(),
        "profile_snapshot": profile,
        "evidence": [],
        "candidate_questions": [],
        "selection": None,
        "decision_log": [],
    }


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def validate_evidence(evidence: Any) -> set[str]:
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    ids: set[str] = set()
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"evidence {index} must be an object")
        evidence_id = require_text(item.get("evidence_id"), f"evidence {index}/id")
        if evidence_id in ids:
            raise ValueError(f"duplicate evidence_id {evidence_id}")
        ids.add(evidence_id)
        url = require_text(item.get("source_url"), f"{evidence_id}/source_url")
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"{evidence_id}: source_url must be HTTP(S)")
        for field in (
            "source_title",
            "source_type",
            "published_date",
            "checked_date",
            "observation",
            "relevance",
        ):
            require_text(item.get(field), f"{evidence_id}/{field}")
    return ids


def validate_candidates(candidates: Any, evidence_ids: set[str]) -> dict[str, dict]:
    if not isinstance(candidates, list):
        raise ValueError("candidate_questions must be a list")
    by_id: dict[str, dict] = {}
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        candidate_id = require_text(
            candidate.get("candidate_id"), f"candidate {index}/candidate_id"
        )
        if candidate_id in by_id:
            raise ValueError(f"duplicate candidate_id {candidate_id}")
        by_id[candidate_id] = candidate
        question = require_text(
            candidate.get("research_question"), f"{candidate_id}/research_question"
        )
        if not question.endswith(("?", "？")):
            raise ValueError(f"{candidate_id}: research_question must be a question")
        for reference_field in ("observation_ids", "closest_prior_ids"):
            references = candidate.get(reference_field)
            if not isinstance(references, list) or not references:
                raise ValueError(f"{candidate_id}/{reference_field} cannot be empty")
            unknown = set(references) - evidence_ids
            if unknown:
                raise ValueError(
                    f"{candidate_id}/{reference_field} has unknown evidence {unknown}"
                )
        for field in (
            "hypothesis",
            "counter_hypothesis",
            "falsification_rule",
            "minimum_experiment",
            "primary_risk",
        ):
            require_text(candidate.get(field), f"{candidate_id}/{field}")
        gates = candidate.get("hard_gates")
        if not isinstance(gates, dict) or set(gates) != set(HARD_GATES):
            raise ValueError(f"{candidate_id}: hard_gates differ from schema")
        if any(type(value) is not bool for value in gates.values()):
            raise ValueError(f"{candidate_id}: hard gates must be boolean")
        scores = candidate.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(SCORE_DIMENSIONS):
            raise ValueError(f"{candidate_id}: scores differ from schema")
        for dimension, score in scores.items():
            if not isinstance(score, dict) or set(score) != {"value", "reason"}:
                raise ValueError(f"{candidate_id}/{dimension}: invalid score")
            if type(score["value"]) is not int or score["value"] not in (0, 1, 2):
                raise ValueError(f"{candidate_id}/{dimension}: value must be 0, 1, or 2")
            require_text(score["reason"], f"{candidate_id}/{dimension}/reason")
    return by_id


def validate_complete_session(session: dict[str, Any]) -> None:
    profile = session["profile_snapshot"]
    defaults = profile["workflow_defaults"]
    evidence_ids = validate_evidence(session["evidence"])
    if len(evidence_ids) < int(defaults["minimum_source_count"]):
        raise ValueError("complete session has too few evidence sources")
    candidates = validate_candidates(session["candidate_questions"], evidence_ids)
    if not (
        int(defaults["minimum_candidate_count"])
        <= len(candidates)
        <= int(defaults["maximum_candidate_count"])
    ):
        raise ValueError("complete session candidate count is outside profile limits")
    selection = session.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("complete session requires selection")
    primary_id = require_text(
        selection.get("primary_candidate_id"), "selection/primary_candidate_id"
    )
    if primary_id not in candidates:
        raise ValueError("selection references an unknown primary candidate")
    if not all(candidates[primary_id]["hard_gates"].values()):
        raise ValueError("primary candidate must pass every hard gate")
    backups = selection.get("backup_candidate_ids")
    expected_backups = int(defaults["backup_question_count"])
    if (
        not isinstance(backups, list)
        or len(backups) != expected_backups
        or len(set(backups)) != expected_backups
        or primary_id in backups
        or not set(backups) <= set(candidates)
    ):
        raise ValueError("selection must contain distinct valid backup candidates")
    require_text(selection.get("why_this_question"), "selection/why_this_question")
    contract = selection.get("research_question_contract")
    if not isinstance(contract, dict) or set(contract) != set(CONTRACT_FIELDS):
        raise ValueError("research_question_contract differs from schema")
    for field in CONTRACT_FIELDS:
        value = contract[field]
        if field in (
            "independent_variables",
            "dependent_variables",
            "controls",
            "boundary_conditions",
            "data_requirements",
        ):
            if not isinstance(value, list) or not value:
                raise ValueError(f"contract/{field} must be a non-empty list")
            for item in value:
                require_text(item, f"contract/{field}")
        else:
            require_text(value, f"contract/{field}")
    if not isinstance(session.get("decision_log"), list) or not session["decision_log"]:
        raise ValueError("complete session requires a decision_log")


def validate_session(session: dict[str, Any], require_complete: bool = False) -> None:
    required = {
        "schema_version",
        "mode_id",
        "status",
        "created_date",
        "profile_snapshot",
        "evidence",
        "candidate_questions",
        "selection",
        "decision_log",
    }
    if set(session) != required:
        raise ValueError("session top-level fields differ from schema")
    if session["schema_version"] != "1.0" or session["mode_id"] != MODE_ID:
        raise ValueError("session identity differs")
    validate_profile(session["profile_snapshot"])
    if require_complete and session.get("status") != "complete":
        raise ValueError("session is not complete")
    if session.get("status") == "complete":
        validate_complete_session(session)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Codex research-question sessions")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    init_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--session", type=Path, required=True)
    validate_parser.add_argument("--complete", action="store_true")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--session", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "init":
        session = initial_session(load_json(args.profile))
        save_json(args.output, session)
        print(json.dumps({"status": session["status"], "path": str(args.output)}))
    else:
        session = load_json(args.session)
        validate_session(
            session, require_complete=args.complete if args.command == "validate" else False
        )
        summary = {
            "status": session["status"],
            "evidence_count": len(session["evidence"]),
            "candidate_count": len(session["candidate_questions"]),
            "has_selection": session["selection"] is not None,
        }
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
