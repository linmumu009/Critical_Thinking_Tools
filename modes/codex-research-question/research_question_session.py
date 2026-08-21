from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = ROOT / "research-profile.json"
DEFAULT_PIPELINE = ROOT / "pipeline-stages.json"
SCHEMA_VERSION = "2.0"
PIPELINE_ID = "research_question_discovery_funnel_v1"
MODES = ("1", "2")
HARD_GATES = (
    "source_grounded",
    "action_divergent",
    "neutral",
    "answerable",
    "ethical",
)
SCORE_DIMENSIONS = (
    "decision_leverage",
    "discriminating_power",
    "reality_grounding",
    "answerability",
    "novelty_nonredundancy",
    "cost_benefit",
)
DECISION_FORK_FIELDS = (
    "decision",
    "answer_a",
    "action_a",
    "answer_b",
    "action_b",
    "uncertain_action",
    "reversal_condition",
)
CHEAP_PROBE_FIELDS = ("input", "procedure", "possible_outcomes", "decision_rules")
CONTRACT_FIELDS = (
    "final_question",
    "triggering_signal_ids",
    "users_and_decision",
    "decision_deadline",
    "key_concepts_and_boundaries",
    "competing_answers",
    "action_mapping",
    "discriminating_evidence",
    "reversal_result",
    "minimum_probe",
    "cost_risk_ethics",
    "stopping_condition",
    "residual_unknowns",
)
LIST_CONTRACT_FIELDS = (
    "triggering_signal_ids",
    "key_concepts_and_boundaries",
    "residual_unknowns",
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


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def require_text_list(value: Any, label: str, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} item(s)")
    return [require_text(item, label) for item in value]


def validate_pipeline(pipeline: dict[str, Any]) -> list[dict[str, Any]]:
    if pipeline.get("pipeline_id") != PIPELINE_ID:
        raise ValueError("pipeline identity differs")
    stages = pipeline.get("stages")
    if not isinstance(stages, list) or len(stages) != 9:
        raise ValueError("shared pipeline must contain stages 0 through 8")
    expected_ids = [
        "0_goal",
        "1_reality_signals",
        "2_reframe",
        "3_expand",
        "4_cluster",
        "5_decision_forks",
        "6_rank",
        "7_probe",
        "8_contract",
    ]
    actual_ids = [stage.get("stage_id") for stage in stages]
    if actual_ids != expected_ids:
        raise ValueError("shared pipeline stage order differs")
    for stage in stages:
        require_text(stage.get("name"), f"{stage['stage_id']}/name")
        require_text(stage.get("required_output"), f"{stage['stage_id']}/required_output")
        tools = stage.get("required_tools")
        if not isinstance(tools, list):
            raise ValueError(f"{stage['stage_id']}/required_tools must be a list")
        for tool in tools:
            require_text(tool, f"{stage['stage_id']}/required_tools")
    return stages


def validate_profile(
    profile: dict[str, Any], pipeline: dict[str, Any] | None = None
) -> None:
    if profile.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("profile schema version differs")
    if profile.get("pipeline_id") != PIPELINE_ID:
        raise ValueError("profile pipeline_id differs")
    if "研究问题" not in str(profile.get("business_goal", "")):
        raise ValueError("profile must fix research-question discovery as the business")
    focus_areas = profile.get("focus_areas")
    if not isinstance(focus_areas, list) or len(focus_areas) < 3:
        raise ValueError("profile must contain at least three focus areas")
    modes = profile.get("mode_definitions")
    if not isinstance(modes, dict) or set(modes) != set(MODES):
        raise ValueError("profile must define exactly modes 1 and 2")
    if modes["1"].get("engine") != "external_model_api":
        raise ValueError("mode 1 must use the external model API engine")
    if modes["2"].get("engine") != "current_codex":
        raise ValueError("mode 2 must use the current Codex engine")
    for mode in MODES:
        adapter = require_text(
            modes[mode].get("adapter_prompt"), f"mode {mode}/adapter_prompt"
        )
        if not (ROOT / adapter).is_file():
            raise ValueError(f"mode {mode} adapter prompt does not exist")
    invariants = profile.get("process_invariants")
    required_invariants = {
        "same_stage_sequence",
        "same_required_tools",
        "same_candidate_schema",
        "same_hard_gates",
        "same_scorecard",
        "same_probe_rules",
        "same_output_contract",
    }
    if not isinstance(invariants, dict):
        raise ValueError("profile process_invariants must be an object")
    if any(invariants.get(key) is not True for key in required_invariants):
        raise ValueError("both modes must share every process invariant")
    constraints = profile.get("execution_constraints", {})
    if constraints.get("mode_2_external_model_api_allowed") is not False:
        raise ValueError("mode 2 cannot call the configured external model API")
    if constraints.get("human_candidate_scoring_required") is not False:
        raise ValueError("neither mode may require user candidate scoring")
    if constraints.get("live_primary_source_search_required") is not True:
        raise ValueError("the shared pipeline requires current primary-source search")
    if pipeline is not None:
        validate_pipeline(pipeline)
        if invariants.get("canonical_stages") != DEFAULT_PIPELINE.name:
            raise ValueError("profile must point to the shared canonical stages")


def mode_definition(profile: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("mode must be 1 or 2")
    return profile["mode_definitions"][mode]


def build_stage_prompt(
    profile: dict[str, Any], pipeline: dict[str, Any], mode: str, stage_id: str
) -> str:
    """Compose an engine adapter with one identical shared stage contract."""
    validate_profile(profile, pipeline)
    stages = validate_pipeline(pipeline)
    stage_by_id = {stage["stage_id"]: stage for stage in stages}
    if stage_id not in stage_by_id:
        raise ValueError(f"unknown stage_id {stage_id}")
    selected_mode = mode_definition(profile, mode)
    adapter = (ROOT / selected_mode["adapter_prompt"]).read_text(encoding="utf-8")
    shared_contract = {
        "pipeline_id": PIPELINE_ID,
        "business_goal": profile["business_goal"],
        "stage": stage_by_id[stage_id],
        "process_invariants": profile["process_invariants"],
    }
    return (
        adapter.rstrip()
        + "\n\n--- SHARED STAGE CONTRACT ---\n"
        + json.dumps(shared_contract, ensure_ascii=False, indent=2)
        + "\n"
    )


def initial_session(
    profile: dict[str, Any], pipeline: dict[str, Any], mode: str
) -> dict[str, Any]:
    validate_profile(profile, pipeline)
    stages = validate_pipeline(pipeline)
    selected_mode = mode_definition(profile, mode)
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_id": PIPELINE_ID,
        "execution": {
            "mode_id": mode,
            "engine": selected_mode["engine"],
            "adapter_prompt": selected_mode["adapter_prompt"],
        },
        "status": "stage_0_goal",
        "created_date": date.today().isoformat(),
        "profile_snapshot": profile,
        "input_manifest": [],
        "stage_trace": [
            {
                "stage_id": stage["stage_id"],
                "status": "pending",
                "output_summary": "",
                "artifact_refs": [],
                "tool_trace": list(stage["required_tools"]),
            }
            for stage in stages
        ],
        "evidence": [],
        "candidate_questions": [],
        "selection": None,
        "decision_log": [],
    }


def validate_input_manifest(manifest: Any, require_complete: bool) -> None:
    if not isinstance(manifest, list):
        raise ValueError("input_manifest must be a list")
    if require_complete and not manifest:
        raise ValueError("complete session requires an input manifest")
    ids: set[str] = set()
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"input {index} must be an object")
        if set(item) != {"input_id", "kind", "location", "role"}:
            raise ValueError(f"input {index} fields differ from schema")
        input_id = require_text(item["input_id"], f"input {index}/input_id")
        if input_id in ids:
            raise ValueError(f"duplicate input_id {input_id}")
        ids.add(input_id)
        for field in ("kind", "location", "role"):
            require_text(item[field], f"{input_id}/{field}")


def validate_stage_trace(
    trace: Any, stages: list[dict[str, Any]], require_complete: bool
) -> None:
    if not isinstance(trace, list) or len(trace) != len(stages):
        raise ValueError("stage_trace must contain every shared stage")
    for expected, actual in zip(stages, trace):
        if not isinstance(actual, dict) or set(actual) != {
            "stage_id",
            "status",
            "output_summary",
            "artifact_refs",
            "tool_trace",
        }:
            raise ValueError("stage_trace item fields differ from schema")
        if actual["stage_id"] != expected["stage_id"]:
            raise ValueError("stage_trace order differs from the shared pipeline")
        if actual["tool_trace"] != expected["required_tools"]:
            raise ValueError(
                f"{actual['stage_id']}: required tool sequence differs from the shared pipeline"
            )
        if actual["status"] not in {"pending", "complete"}:
            raise ValueError(f"{actual['stage_id']}: invalid stage status")
        if require_complete:
            if actual["status"] != "complete":
                raise ValueError(f"{actual['stage_id']}: complete session has pending stage")
            require_text(actual["output_summary"], f"{actual['stage_id']}/output_summary")
            require_text_list(
                actual["artifact_refs"], f"{actual['stage_id']}/artifact_refs"
            )


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
        location = require_text(
            item.get("source_location"), f"{evidence_id}/source_location"
        )
        if not (
            location.startswith(("https://", "http://"))
            or Path(location).is_absolute()
        ):
            raise ValueError(
                f"{evidence_id}: source_location must be HTTP(S) or an absolute path"
            )
        for field in (
            "source_title",
            "source_type",
            "published_date",
            "checked_date",
            "observation",
            "interpretation",
            "unknown",
        ):
            require_text(item.get(field), f"{evidence_id}/{field}")
    return ids


def validate_candidates(candidates: Any, evidence_ids: set[str]) -> dict[str, dict]:
    if not isinstance(candidates, list):
        raise ValueError("candidate_questions must be a list")
    by_id: dict[str, dict] = {}
    required_fields = {
        "candidate_id",
        "research_question",
        "signal_ids",
        "closest_prior_ids",
        "question_family",
        "cluster_id",
        "decision_fork",
        "evidence_path",
        "cheap_probe",
        "probe_disposition",
        "hard_gates",
        "scores",
    }
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict) or set(candidate) != required_fields:
            raise ValueError(f"candidate {index} fields differ from schema")
        candidate_id = require_text(
            candidate["candidate_id"], f"candidate {index}/candidate_id"
        )
        if candidate_id in by_id:
            raise ValueError(f"duplicate candidate_id {candidate_id}")
        by_id[candidate_id] = candidate
        question = require_text(
            candidate["research_question"], f"{candidate_id}/research_question"
        )
        if not question.endswith(("?", "？")):
            raise ValueError(f"{candidate_id}: research_question must be a question")
        for reference_field in ("signal_ids", "closest_prior_ids"):
            references = require_text_list(
                candidate[reference_field], f"{candidate_id}/{reference_field}"
            )
            unknown = set(references) - evidence_ids
            if unknown:
                raise ValueError(
                    f"{candidate_id}/{reference_field} has unknown evidence {unknown}"
                )
        require_text(candidate["question_family"], f"{candidate_id}/question_family")
        require_text(candidate["cluster_id"], f"{candidate_id}/cluster_id")
        require_text(candidate["evidence_path"], f"{candidate_id}/evidence_path")
        fork = candidate["decision_fork"]
        if not isinstance(fork, dict) or set(fork) != set(DECISION_FORK_FIELDS):
            raise ValueError(f"{candidate_id}/decision_fork differs from schema")
        for field in DECISION_FORK_FIELDS:
            require_text(fork[field], f"{candidate_id}/decision_fork/{field}")
        probe = candidate["cheap_probe"]
        if not isinstance(probe, dict) or set(probe) != set(CHEAP_PROBE_FIELDS):
            raise ValueError(f"{candidate_id}/cheap_probe differs from schema")
        require_text(probe["input"], f"{candidate_id}/cheap_probe/input")
        require_text(probe["procedure"], f"{candidate_id}/cheap_probe/procedure")
        require_text_list(
            probe["possible_outcomes"],
            f"{candidate_id}/cheap_probe/possible_outcomes",
            minimum=2,
        )
        rules = probe["decision_rules"]
        if not isinstance(rules, dict) or set(rules) != {
            "keep",
            "narrow",
            "rewrite",
            "reject",
        }:
            raise ValueError(f"{candidate_id}/cheap_probe/decision_rules differs")
        for decision, rule in rules.items():
            require_text(rule, f"{candidate_id}/cheap_probe/{decision}")
        if candidate["probe_disposition"] not in {
            "keep",
            "narrow",
            "rewrite",
            "reject",
        }:
            raise ValueError(f"{candidate_id}/probe_disposition is invalid")
        gates = candidate["hard_gates"]
        if not isinstance(gates, dict) or set(gates) != set(HARD_GATES):
            raise ValueError(f"{candidate_id}: hard_gates differ from the scorecard")
        if any(type(value) is not bool for value in gates.values()):
            raise ValueError(f"{candidate_id}: hard gates must be boolean")
        scores = candidate["scores"]
        if not isinstance(scores, dict) or set(scores) != set(SCORE_DIMENSIONS):
            raise ValueError(f"{candidate_id}: scores differ from the scorecard")
        for dimension, score in scores.items():
            if not isinstance(score, dict) or set(score) != {"value", "reason"}:
                raise ValueError(f"{candidate_id}/{dimension}: invalid score")
            if type(score["value"]) is not int or score["value"] not in (0, 1, 2):
                raise ValueError(f"{candidate_id}/{dimension}: value must be 0, 1, or 2")
            require_text(score["reason"], f"{candidate_id}/{dimension}/reason")
    return by_id


def validate_contract(contract: Any, evidence_ids: set[str]) -> None:
    if not isinstance(contract, dict) or set(contract) != set(CONTRACT_FIELDS):
        raise ValueError("research_question_contract differs from schema")
    for field in LIST_CONTRACT_FIELDS:
        require_text_list(contract[field], f"contract/{field}")
    unknown_signals = set(contract["triggering_signal_ids"]) - evidence_ids
    if unknown_signals:
        raise ValueError(f"contract references unknown signals {unknown_signals}")
    for field in set(CONTRACT_FIELDS) - set(LIST_CONTRACT_FIELDS) - {
        "competing_answers",
        "action_mapping",
    }:
        require_text(contract[field], f"contract/{field}")
    answers = contract["competing_answers"]
    if not isinstance(answers, dict) or set(answers) != {"a", "b", "unknown"}:
        raise ValueError("contract/competing_answers differs from schema")
    actions = contract["action_mapping"]
    if not isinstance(actions, dict) or set(actions) != {
        "if_a",
        "if_b",
        "if_uncertain",
    }:
        raise ValueError("contract/action_mapping differs from schema")
    for key, value in answers.items():
        require_text(value, f"contract/competing_answers/{key}")
    for key, value in actions.items():
        require_text(value, f"contract/action_mapping/{key}")


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
    if not isinstance(selection, dict) or set(selection) != {
        "primary_candidate_id",
        "backup_candidate_ids",
        "why_this_question",
        "research_question_contract",
    }:
        raise ValueError("complete session requires a valid selection")
    primary_id = require_text(
        selection["primary_candidate_id"], "selection/primary_candidate_id"
    )
    backups = selection["backup_candidate_ids"]
    expected_backups = int(defaults["backup_question_count"])
    if (
        primary_id not in candidates
        or not isinstance(backups, list)
        or len(backups) != expected_backups
        or len(set(backups)) != expected_backups
        or primary_id in backups
        or not set(backups) <= set(candidates)
    ):
        raise ValueError("selection must contain one primary and distinct valid backups")
    for selected_id in [primary_id, *backups]:
        candidate = candidates[selected_id]
        if not all(candidate["hard_gates"].values()):
            raise ValueError("every selected candidate must pass every hard gate")
        if candidate["probe_disposition"] == "reject":
            raise ValueError("a rejected probe candidate cannot be selected")
    require_text(selection["why_this_question"], "selection/why_this_question")
    validate_contract(selection["research_question_contract"], evidence_ids)
    if not isinstance(session.get("decision_log"), list) or not session["decision_log"]:
        raise ValueError("complete session requires a decision_log")


def validate_session(
    session: dict[str, Any],
    profile: dict[str, Any],
    pipeline: dict[str, Any],
    require_complete: bool = False,
) -> None:
    if session.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "legacy autonomous session: historical artifact, not a valid shared-pipeline mode run"
        )
    required = {
        "schema_version",
        "pipeline_id",
        "execution",
        "status",
        "created_date",
        "profile_snapshot",
        "input_manifest",
        "stage_trace",
        "evidence",
        "candidate_questions",
        "selection",
        "decision_log",
    }
    if set(session) != required:
        raise ValueError("session top-level fields differ from schema")
    validate_profile(profile, pipeline)
    if session["pipeline_id"] != PIPELINE_ID:
        raise ValueError("session pipeline identity differs")
    if session["profile_snapshot"] != profile:
        raise ValueError("session profile snapshot differs from the shared profile")
    execution = session["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "mode_id",
        "engine",
        "adapter_prompt",
    }:
        raise ValueError("session execution fields differ from schema")
    selected_mode = mode_definition(profile, str(execution["mode_id"]))
    if (
        execution["engine"] != selected_mode["engine"]
        or execution["adapter_prompt"] != selected_mode["adapter_prompt"]
    ):
        raise ValueError("session engine differs from the selected mode adapter")
    stages = validate_pipeline(pipeline)
    effective_complete = require_complete or session.get("status") == "complete"
    validate_input_manifest(session["input_manifest"], effective_complete)
    validate_stage_trace(session["stage_trace"], stages, effective_complete)
    if require_complete and session.get("status") != "complete":
        raise ValueError("session is not complete")
    if session.get("status") == "complete":
        validate_complete_session(session)


def next_stage_id(session: dict[str, Any]) -> str | None:
    for stage in session["stage_trace"]:
        if stage["status"] != "complete":
            return stage["stage_id"]
    return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Manage shared research-question discovery sessions"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--mode", choices=MODES, required=True)
    init_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    init_parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE)
    init_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--session", type=Path, required=True)
    validate_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    validate_parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE)
    validate_parser.add_argument("--complete", action="store_true")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--session", type=Path, required=True)
    status_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    status_parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE)
    prompt_parser = subparsers.add_parser("prompt")
    prompt_parser.add_argument("--mode", choices=MODES, required=True)
    prompt_parser.add_argument(
        "--stage",
        choices=[stage["stage_id"] for stage in validate_pipeline(load_json(DEFAULT_PIPELINE))],
    )
    prompt_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()

    profile = load_json(args.profile)
    if args.command == "prompt":
        pipeline = load_json(DEFAULT_PIPELINE)
        if args.stage:
            print(build_stage_prompt(profile, pipeline, args.mode, args.stage), end="")
        else:
            validate_profile(profile, pipeline)
            adapter = ROOT / mode_definition(profile, args.mode)["adapter_prompt"]
            print(adapter.read_text(encoding="utf-8"), end="")
        return 0

    pipeline = load_json(args.pipeline)
    if args.command == "init":
        session = initial_session(profile, pipeline, args.mode)
        save_json(args.output, session)
        print(
            json.dumps(
                {
                    "status": session["status"],
                    "mode": args.mode,
                    "engine": session["execution"]["engine"],
                    "path": str(args.output),
                },
                ensure_ascii=False,
            )
        )
        return 0

    session = load_json(args.session)
    validate_session(
        session,
        profile,
        pipeline,
        require_complete=args.complete if args.command == "validate" else False,
    )
    print(
        json.dumps(
            {
                "status": session["status"],
                "mode": session["execution"]["mode_id"],
                "engine": session["execution"]["engine"],
                "next_stage": next_stage_id(session),
                "evidence_count": len(session["evidence"]),
                "candidate_count": len(session["candidate_questions"]),
                "has_selection": session["selection"] is not None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
