from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = ROOT / "research-profile.json"
DEFAULT_PIPELINE = ROOT / "pipeline-stages.json"
SCHEMA_VERSION = "2.1"
LEGACY_SCHEMA_VERSION = "2.0"
PIPELINE_ID = "research_question_discovery_funnel_v1"
MODES = ("1", "2")
HARD_GATES = (
    "source_grounded",
    "action_divergent",
    "neutral",
    "answerable",
    "ethical",
    "readable",
    "atomic",
    "low_concept_burden",
    "value_visible",
)
LEGACY_HARD_GATES = HARD_GATES[:5]
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
    "short_title",
    "plain_question",
    "why_it_matters",
    "final_question",
    "question_identity",
    "secondary_questions",
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
LEGACY_CONTRACT_FIELDS = tuple(
    field
    for field in CONTRACT_FIELDS
    if field
    not in {
        "short_title",
        "plain_question",
        "why_it_matters",
        "question_identity",
        "secondary_questions",
    }
)
LIST_CONTRACT_FIELDS = (
    "triggering_signal_ids",
    "key_concepts_and_boundaries",
    "residual_unknowns",
)
QUESTION_IDENTITY_FIELDS = (
    "unit_of_analysis",
    "comparison",
    "outcome",
    "scope",
)
SECONDARY_QUESTION_FIELDS = ("mechanism", "boundary", "intervention")
MAX_SHORT_TITLE_LENGTH = 30
MAX_PLAIN_QUESTION_LENGTH = 80
MAX_FORMAL_QUESTION_LENGTH = 120
MAX_VALUE_STATEMENT_LENGTH = 160


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


def require_bounded_text(value: Any, label: str, maximum: int) -> str:
    text = require_text(value, label)
    if len(normalized_question(text)) > maximum:
        raise ValueError(f"{label} exceeds {maximum} normalized characters")
    return text


def validate_question_identity(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(QUESTION_IDENTITY_FIELDS):
        raise ValueError(f"{label} differs from schema")
    for field in QUESTION_IDENTITY_FIELDS:
        require_text(value[field], f"{label}/{field}")
    return value


def validate_secondary_questions(value: Any, label: str) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != set(SECONDARY_QUESTION_FIELDS):
        raise ValueError(f"{label} differs from schema")
    for field in SECONDARY_QUESTION_FIELDS:
        questions = value[field]
        if not isinstance(questions, list):
            raise ValueError(f"{label}/{field} must be a list")
        for question in questions:
            text = require_text(question, f"{label}/{field}")
            if not text.endswith(("?", "？")):
                raise ValueError(f"{label}/{field} items must be questions")
    return value


def interrogative_count(value: str) -> int:
    return sum(
        len(re.findall(pattern, value, flags=re.IGNORECASE))
        for pattern in (r"是否", r"能否", r"何时", r"为什么", r"为何", r"如何", r"\bdoes\b", r"\bcan\b", r"\bwhy\b", r"\bhow\b")
    )


def bundles_effect_and_mechanism(value: str) -> bool:
    return bool(
        re.search(
            r"是否.{0,80}通过.{0,80}(提高|降低|改变|影响|导致|改善|恶化)",
            value,
        )
    )


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
    profile_version = profile.get("schema_version")
    if profile_version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
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
    if constraints.get("selected_question_common_knowledge_review_required") is not True:
        raise ValueError("selected questions require a common-knowledge review")
    if constraints.get("selected_question_prior_art_review_required") is not True:
        raise ValueError("selected questions require a prior-art review")
    if constraints.get("minimum_prior_art_query_families") != 3:
        raise ValueError("prior-art review requires three query families")
    if profile_version == SCHEMA_VERSION:
        if constraints.get("previous_questions_are_active_baselines") is not True:
            raise ValueError("previous winners must remain active comparison baselines")
        if constraints.get("no_better_question_outcome_allowed") is not True:
            raise ValueError("the shared pipeline must allow a no-better-question outcome")
        if constraints.get("same_experiment_requires_question_merge") is not False:
            raise ValueError("sharing an experiment cannot require question merging")
        if constraints.get("novelty_by_constraint_conjunction_allowed") is not False:
            raise ValueError("novelty by constraint conjunction must be forbidden")
        if constraints.get("layered_question_presentation_required") is not True:
            raise ValueError("layered question presentation must be required")
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


def validate_candidates(
    candidates: Any, evidence_ids: set[str], schema_version: str = SCHEMA_VERSION
) -> dict[str, dict]:
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
    if schema_version == SCHEMA_VERSION:
        required_fields |= {
            "short_title",
            "plain_question",
            "why_it_matters",
            "question_identity",
            "secondary_questions",
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
        if schema_version == SCHEMA_VERSION:
            require_bounded_text(
                candidate["short_title"],
                f"{candidate_id}/short_title",
                MAX_SHORT_TITLE_LENGTH,
            )
            plain_question = require_bounded_text(
                candidate["plain_question"],
                f"{candidate_id}/plain_question",
                MAX_PLAIN_QUESTION_LENGTH,
            )
            if not plain_question.endswith(("?", "？")):
                raise ValueError(f"{candidate_id}: plain_question must be a question")
            require_bounded_text(
                question,
                f"{candidate_id}/research_question",
                MAX_FORMAL_QUESTION_LENGTH,
            )
            require_bounded_text(
                candidate["why_it_matters"],
                f"{candidate_id}/why_it_matters",
                MAX_VALUE_STATEMENT_LENGTH,
            )
            validate_question_identity(
                candidate["question_identity"], f"{candidate_id}/question_identity"
            )
            validate_secondary_questions(
                candidate["secondary_questions"],
                f"{candidate_id}/secondary_questions",
            )
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
        expected_gates = (
            set(HARD_GATES)
            if schema_version == SCHEMA_VERSION
            else set(LEGACY_HARD_GATES)
        )
        if not isinstance(gates, dict) or set(gates) != expected_gates:
            raise ValueError(f"{candidate_id}: hard_gates differ from the scorecard")
        if any(type(value) is not bool for value in gates.values()):
            raise ValueError(f"{candidate_id}: hard gates must be boolean")
        if schema_version == SCHEMA_VERSION:
            if gates["atomic"] and (
                interrogative_count(question) > 1
                or any(mark in question for mark in (";", "；"))
                or bundles_effect_and_mechanism(question)
            ):
                raise ValueError(
                    f"{candidate_id}: atomic gate conflicts with a compound research question"
                )
            acronym_count = len(re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", plain_question))
            if gates["low_concept_burden"] and acronym_count > 3:
                raise ValueError(
                    f"{candidate_id}: plain question exceeds the acronym burden"
                )
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


def validate_contract(
    contract: Any, evidence_ids: set[str], schema_version: str = SCHEMA_VERSION
) -> None:
    expected_fields = (
        set(CONTRACT_FIELDS)
        if schema_version == SCHEMA_VERSION
        else set(LEGACY_CONTRACT_FIELDS)
    )
    if not isinstance(contract, dict) or set(contract) != expected_fields:
        raise ValueError("research_question_contract differs from schema")
    for field in LIST_CONTRACT_FIELDS:
        require_text_list(contract[field], f"contract/{field}")
    unknown_signals = set(contract["triggering_signal_ids"]) - evidence_ids
    if unknown_signals:
        raise ValueError(f"contract references unknown signals {unknown_signals}")
    for field in expected_fields - set(LIST_CONTRACT_FIELDS) - {
        "competing_answers",
        "action_mapping",
        "question_identity",
        "secondary_questions",
    }:
        require_text(contract[field], f"contract/{field}")
    if schema_version == SCHEMA_VERSION:
        require_bounded_text(
            contract["short_title"], "contract/short_title", MAX_SHORT_TITLE_LENGTH
        )
        plain_question = require_bounded_text(
            contract["plain_question"],
            "contract/plain_question",
            MAX_PLAIN_QUESTION_LENGTH,
        )
        if not plain_question.endswith(("?", "？")):
            raise ValueError("contract/plain_question must be a question")
        require_bounded_text(
            contract["why_it_matters"],
            "contract/why_it_matters",
            MAX_VALUE_STATEMENT_LENGTH,
        )
        final_question = require_bounded_text(
            contract["final_question"],
            "contract/final_question",
            MAX_FORMAL_QUESTION_LENGTH,
        )
        if not final_question.endswith(("?", "？")):
            raise ValueError("contract/final_question must be a question")
        validate_question_identity(
            contract["question_identity"], "contract/question_identity"
        )
        validate_secondary_questions(
            contract["secondary_questions"], "contract/secondary_questions"
        )
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


def validate_incumbent_comparison(value: Any, outcome: str) -> None:
    required = {"incumbent_question_refs", "outcome", "reason"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("selection/incumbent_comparison differs from schema")
    references = value["incumbent_question_refs"]
    if not isinstance(references, list):
        raise ValueError("incumbent_question_refs must be a list")
    for reference in references:
        require_text(reference, "selection/incumbent_question_refs")
    if value["outcome"] not in {
        "first-run",
        "new-question-better",
        "no-better-question",
    }:
        raise ValueError("selection/incumbent_comparison outcome is invalid")
    require_text(value["reason"], "selection/incumbent_comparison/reason")
    if outcome == "no_better_question":
        if not references or value["outcome"] != "no-better-question":
            raise ValueError(
                "no-better-question requires at least one incumbent and a matching comparison"
            )
    elif references and value["outcome"] != "new-question-better":
        raise ValueError("selected new question must explicitly beat listed incumbents")
    elif not references and value["outcome"] != "first-run":
        raise ValueError("a run without incumbents must be marked first-run")


def validate_complete_session(session: dict[str, Any]) -> None:
    schema_version = session["schema_version"]
    profile = session["profile_snapshot"]
    defaults = profile["workflow_defaults"]
    evidence_ids = validate_evidence(session["evidence"])
    if len(evidence_ids) < int(defaults["minimum_source_count"]):
        raise ValueError("complete session has too few evidence sources")
    candidates = validate_candidates(
        session["candidate_questions"], evidence_ids, schema_version
    )
    if not (
        int(defaults["minimum_candidate_count"])
        <= len(candidates)
        <= int(defaults["maximum_candidate_count"])
    ):
        raise ValueError("complete session candidate count is outside profile limits")
    selection = session.get("selection")
    expected_selection_fields = {
        "primary_candidate_id",
        "backup_candidate_ids",
        "why_this_question",
        "research_question_contract",
    }
    if schema_version == SCHEMA_VERSION:
        expected_selection_fields |= {"outcome", "incumbent_comparison"}
    if not isinstance(selection, dict) or set(selection) != expected_selection_fields:
        raise ValueError("complete session requires a valid selection")
    outcome = (
        selection["outcome"] if schema_version == SCHEMA_VERSION else "selected"
    )
    if outcome not in {"selected", "no_better_question"}:
        raise ValueError("selection/outcome is invalid")
    require_text(selection["why_this_question"], "selection/why_this_question")
    if schema_version == SCHEMA_VERSION:
        validate_incumbent_comparison(selection["incumbent_comparison"], outcome)
    if outcome == "no_better_question":
        if (
            selection["primary_candidate_id"] is not None
            or selection["backup_candidate_ids"] != []
            or selection["research_question_contract"] is not None
        ):
            raise ValueError(
                "no-better-question outcome cannot contain selected candidates or a contract"
            )
        if not isinstance(session.get("decision_log"), list) or not session["decision_log"]:
            raise ValueError("complete session requires a decision_log")
        return
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
    validate_contract(
        selection["research_question_contract"], evidence_ids, schema_version
    )
    if not isinstance(session.get("decision_log"), list) or not session["decision_log"]:
        raise ValueError("complete session requires a decision_log")


def validate_session(
    session: dict[str, Any],
    profile: dict[str, Any],
    pipeline: dict[str, Any],
    require_complete: bool = False,
) -> None:
    session_version = session.get("schema_version")
    if session_version == "1.0":
        raise ValueError(
            "legacy autonomous session: historical artifact, not a valid shared-pipeline mode run"
        )
    if session_version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise ValueError("unsupported session schema version")
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
    effective_profile = (
        session["profile_snapshot"]
        if session_version == LEGACY_SCHEMA_VERSION
        else profile
    )
    validate_profile(effective_profile, pipeline)
    if session["pipeline_id"] != PIPELINE_ID:
        raise ValueError("session pipeline identity differs")
    if session_version == SCHEMA_VERSION and session["profile_snapshot"] != profile:
        raise ValueError("session profile snapshot differs from the shared profile")
    execution = session["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "mode_id",
        "engine",
        "adapter_prompt",
    }:
        raise ValueError("session execution fields differ from schema")
    selected_mode = mode_definition(effective_profile, str(execution["mode_id"]))
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


def score_total(candidate: dict[str, Any]) -> int:
    return sum(int(item["value"]) for item in candidate["scores"].values())


def normalized_question(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("?", "？")


def character_bigrams(value: str) -> set[str]:
    normalized = normalized_question(value).lower()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def question_similarity(left: str, right: str) -> float:
    left_parts = character_bigrams(left)
    right_parts = character_bigrams(right)
    if not left_parts or not right_parts:
        return float(normalized_question(left) == normalized_question(right))
    return len(left_parts & right_parts) / len(left_parts | right_parts)


def semantic_audit(session: dict[str, Any]) -> dict[str, Any]:
    """Audit cross-field meaning that the structural validator cannot express."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add(target: list[dict[str, str]], code: str, message: str) -> None:
        target.append({"code": code, "message": message})

    if session.get("status") != "complete":
        add(errors, "session_not_complete", "semantic audit requires a complete session")
        return {"passed": False, "errors": errors, "warnings": warnings, "metrics": {}}

    candidates = session["candidate_questions"]
    by_id = {item["candidate_id"]: item for item in candidates}
    selection = session["selection"]
    if selection.get("outcome") == "no_better_question":
        if candidates and all(all(item["hard_gates"].values()) for item in candidates):
            add(
                warnings,
                "hard_gate_saturation",
                "every candidate passes every hard gate; review whether generation and screening were actually separated",
            )
        score_totals = [score_total(item) for item in candidates]
        if len(set(score_totals)) == 1 and len(score_totals) > 1:
            add(
                warnings,
                "score_saturation",
                "all candidates have the same score; treat self-scoring as preliminary rather than evidence",
            )
        evidence_locations: dict[str, str] = {}
        for item in session["evidence"]:
            location = item["source_location"].strip().rstrip("/").lower()
            if location in evidence_locations:
                add(
                    warnings,
                    "duplicate_evidence_location",
                    f"{item['evidence_id']} duplicates {evidence_locations[location]}",
                )
            else:
                evidence_locations[location] = item["evidence_id"]
            try:
                date.fromisoformat(item["checked_date"])
            except ValueError:
                add(
                    errors,
                    "invalid_checked_date",
                    f"{item['evidence_id']} checked_date must be ISO YYYY-MM-DD",
                )
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "metrics": {
                "candidate_count": len(candidates),
                "evidence_count": len(session["evidence"]),
                "eligible_candidate_count": sum(
                    all(item["hard_gates"].values())
                    and item["probe_disposition"] != "reject"
                    for item in candidates
                ),
                "selection_outcome": "no_better_question",
                "unique_evidence_locations": len(evidence_locations),
            },
        }
    primary = by_id[selection["primary_candidate_id"]]
    selected_ids = [selection["primary_candidate_id"], *selection["backup_candidate_ids"]]
    contract = selection["research_question_contract"]

    if session["schema_version"] == SCHEMA_VERSION:
        if contract["question_identity"] != primary["question_identity"]:
            add(
                errors,
                "contract_question_identity_mismatch",
                "the final contract changes the primary question identity",
            )
        if contract["secondary_questions"] != primary["secondary_questions"]:
            add(
                errors,
                "contract_secondary_question_drift",
                "the final contract changes the primary candidate's question tree",
            )
    elif normalized_question(contract["final_question"]) != normalized_question(primary["research_question"]):
        similarity = question_similarity(
            contract["final_question"], primary["research_question"]
        )
        if similarity < 0.55:
            add(errors, "contract_question_mismatch", "the final contract question materially drifts from the selected primary question")
        else:
            add(
                warnings,
                "contract_question_rephrased",
                f"the final contract rephrases the primary question (similarity {similarity:.2f}); review the refinement",
            )

    contract_signals = set(contract["triggering_signal_ids"])
    if not contract_signals <= set(primary["signal_ids"]):
        add(errors, "contract_signal_drift", "the final contract introduces triggering signals absent from the primary candidate")

    eligible = [
        item
        for item in candidates
        if all(item["hard_gates"].values()) and item["probe_disposition"] != "reject"
    ]
    top_score = max(score_total(item) for item in eligible)
    if candidates and all(all(item["hard_gates"].values()) for item in candidates):
        add(
            warnings,
            "hard_gate_saturation",
            "every candidate passes every hard gate; review whether generation and screening were actually separated",
        )
    score_totals = [score_total(item) for item in candidates]
    if len(set(score_totals)) == 1 and len(score_totals) > 1:
        add(
            warnings,
            "score_saturation",
            "all candidates have the same score; treat self-scoring as preliminary rather than evidence",
        )
    if score_total(primary) < top_score:
        add(
            warnings,
            "primary_not_top_score",
            f"primary score {score_total(primary)} is below eligible maximum {top_score}; the rationale must explain the override",
        )

    for candidate_id in selected_ids:
        candidate = by_id[candidate_id]
        if not candidate["closest_prior_ids"]:
            add(errors, "selected_without_closest_prior", f"{candidate_id} has no closest-prior reference")
        fork = candidate["decision_fork"]
        if normalized_question(fork["action_a"]) == normalized_question(fork["action_b"]):
            add(errors, "nondivergent_actions", f"{candidate_id} maps answers A and B to the same action")

    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            similarity = question_similarity(left["research_question"], right["research_question"])
            if similarity >= 0.82:
                add(
                    warnings,
                    "near_duplicate_candidates",
                    f"{left['candidate_id']} and {right['candidate_id']} have character-bigram Jaccard similarity {similarity:.2f}",
                )

    evidence_locations: dict[str, str] = {}
    for item in session["evidence"]:
        location = item["source_location"].strip().rstrip("/").lower()
        if location in evidence_locations:
            add(warnings, "duplicate_evidence_location", f"{item['evidence_id']} duplicates {evidence_locations[location]}")
        else:
            evidence_locations[location] = item["evidence_id"]
        try:
            date.fromisoformat(item["checked_date"])
        except ValueError:
            add(errors, "invalid_checked_date", f"{item['evidence_id']} checked_date must be ISO YYYY-MM-DD")

    selected_scores = {candidate_id: score_total(by_id[candidate_id]) for candidate_id in selected_ids}
    metrics = {
        "candidate_count": len(candidates),
        "evidence_count": len(session["evidence"]),
        "eligible_candidate_count": len(eligible),
        "selected_scores": selected_scores,
        "top_eligible_score": top_score,
        "unique_evidence_locations": len(evidence_locations),
        "selection_outcome": "selected",
    }
    return {"passed": not errors, "errors": errors, "warnings": warnings, "metrics": metrics}


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
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--session", type=Path, required=True)
    audit_parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    audit_parser.add_argument("--pipeline", type=Path, default=DEFAULT_PIPELINE)
    audit_parser.add_argument("--strict", action="store_true")
    audit_parser.add_argument("--output", type=Path)
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
        require_complete=(
            args.complete if args.command == "validate" else args.command == "audit"
        ),
    )
    if args.command == "audit":
        result = semantic_audit(session)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            save_json(args.output, result)
        print(rendered)
        return int(not result["passed"] or (args.strict and bool(result["warnings"])))
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
