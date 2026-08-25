from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_question_session as rqs


ROOT = Path(__file__).resolve().parent
RUNS_ROOT = ROOT / "runs"
RUNNER_VERSION = "1.0"
LEDGER_VERSION = "1.0"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
STAGE_UPDATE_FIELDS = {
    "0_goal": {"input_manifest", "decision_log"},
    "1_reality_signals": {"input_manifest", "evidence", "decision_log"},
    "2_reframe": {"decision_log"},
    "3_expand": {"decision_log"},
    "4_cluster": {"candidate_questions", "decision_log"},
    "5_decision_forks": {"candidate_questions", "decision_log"},
    "6_rank": {"candidate_questions", "decision_log"},
    "7_probe": {"candidate_questions", "decision_log"},
    "8_contract": {"selection", "decision_log"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value: dict[str, Any]) -> None:
    rqs.save_json(path, value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_run(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path.resolve()
    return (RUNS_ROOT / path).resolve()


def run_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "state": run_dir / "run-state.json",
        "session": run_dir / "session.json",
        "ledger": run_dir / "evidence-ledger.json",
        "stages": run_dir / "stages",
        "packets": run_dir / "packets",
    }


def load_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = run_paths(run_dir)
    if not paths["state"].is_file():
        raise ValueError(f"run state not found: {paths['state']}")
    state = rqs.load_json(paths["state"])
    session = rqs.load_json(paths["session"])
    ledger = rqs.load_json(paths["ledger"])
    if ledger.get("run_id") != state.get("run_id"):
        raise ValueError("run state and evidence ledger identities differ")
    execution = session.get("execution", {})
    if (
        str(execution.get("mode_id")) != state.get("mode")
        or execution.get("engine") != state.get("engine")
    ):
        raise ValueError("run state and session engine identities differ")

    return state, session, ledger


def parse_input_manifest(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("input manifest file must contain a JSON array")
    rqs.validate_input_manifest(value, require_complete=False)
    return value


def initialize_run(
    run_id: str,
    mode: str,
    run_dir: Path,
    input_manifest: list[dict[str, str]],
) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must use 1-80 letters, digits, dots, underscores, or hyphens")
    if run_dir.exists():
        raise ValueError(f"run directory already exists: {run_dir}")
    profile = rqs.load_json(rqs.DEFAULT_PROFILE)
    pipeline = rqs.load_json(rqs.DEFAULT_PIPELINE)
    session = rqs.initial_session(profile, pipeline, mode)
    session["input_manifest"] = input_manifest
    paths = run_paths(run_dir)
    paths["stages"].mkdir(parents=True)
    paths["packets"].mkdir(parents=True)
    now = utc_now()
    state = {
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "mode": mode,
        "engine": session["execution"]["engine"],
        "status": "active",
        "created_at_utc": now,
        "updated_at_utc": now,
        "current_stage": rqs.next_stage_id(session),
        "stage_order": [item["stage_id"] for item in session["stage_trace"]],
        "checkpoints": [],
        "failures": [],
    }
    ledger = {
        "ledger_version": LEDGER_VERSION,
        "run_id": run_id,
        "created_at_utc": now,
        "updated_at_utc": now,
        "queries": [],
        "source_decisions": [],
        "collision_reviews": [],
    }
    save_json(paths["session"], session)
    save_json(paths["state"], state)
    save_json(paths["ledger"], ledger)
    return state


def build_packet(run_dir: Path) -> Path:
    state, session, _ = load_run(run_dir)
    stage_id = rqs.next_stage_id(session)
    if stage_id is None:
        raise ValueError("all stages are complete; run audit/finalize instead")
    profile = rqs.load_json(rqs.DEFAULT_PROFILE)
    pipeline = rqs.load_json(rqs.DEFAULT_PIPELINE)
    stage = next(item for item in pipeline["stages"] if item["stage_id"] == stage_id)
    packet = {
        "packet_version": "1.0",
        "run_id": state["run_id"],
        "mode": state["mode"],
        "engine": state["engine"],
        "stage_id": stage_id,
        "created_at_utc": utc_now(),
        "stage_contract": stage,
        "engine_prompt": rqs.build_stage_prompt(profile, pipeline, state["mode"], stage_id),
        "session_path": str(run_paths(run_dir)["session"]),
        "ledger_path": str(run_paths(run_dir)["ledger"]),
        "completed_stage_artifacts": [
            item["artifact_path"] for item in state["checkpoints"]
        ],
        "output_contract": {
            "schema_version": "1.0",
            "required_fields": [
                "schema_version",
                "run_id",
                "stage_id",
                "output_summary",
                "artifact_refs",
                "payload",
                "session_updates",
            ],
            "allowed_session_update_fields": sorted(STAGE_UPDATE_FIELDS[stage_id]),
            "instruction": (
                "Write one JSON envelope. Do not mark a later stage complete. "
                "Record search queries and collision decisions through the ledger commands."
            ),
        },
    }
    output = run_paths(run_dir)["packets"] / f"{stage_id}.json"
    save_json(output, packet)
    return output


def validate_envelope(
    envelope: dict[str, Any], state: dict[str, Any], expected_stage: str
) -> None:
    required = {
        "schema_version",
        "run_id",
        "stage_id",
        "output_summary",
        "artifact_refs",
        "payload",
        "session_updates",
    }
    if set(envelope) != required:
        raise ValueError("stage envelope fields differ from the runner contract")
    if envelope["schema_version"] != "1.0":
        raise ValueError("unsupported stage envelope schema")
    if envelope["run_id"] != state["run_id"] or envelope["stage_id"] != expected_stage:
        raise ValueError("stage envelope belongs to another run or stage")
    rqs.require_text(envelope["output_summary"], "output_summary")
    rqs.require_text_list(envelope["artifact_refs"], "artifact_refs")
    if not isinstance(envelope["payload"], dict):
        raise ValueError("payload must be a JSON object")
    updates = envelope["session_updates"]
    if not isinstance(updates, dict):
        raise ValueError("session_updates must be a JSON object")
    unknown = set(updates) - STAGE_UPDATE_FIELDS[expected_stage]
    if unknown:
        raise ValueError(f"{expected_stage} cannot update session fields {sorted(unknown)}")


def merge_unique(
    existing: list[dict[str, Any]], additions: list[dict[str, Any]], id_field: str
) -> list[dict[str, Any]]:
    result = list(existing)
    ids = {item[id_field] for item in result}
    for item in additions:
        item_id = item.get(id_field)
        if item_id in ids:
            raise ValueError(f"duplicate {id_field}: {item_id}")
        result.append(item)
        ids.add(item_id)
    return result


def apply_session_updates(
    session: dict[str, Any], stage_id: str, updates: dict[str, Any]
) -> None:
    if "input_manifest" in updates:
        if not isinstance(updates["input_manifest"], list):
            raise ValueError("input_manifest update must be a list")
        session["input_manifest"] = merge_unique(
            session["input_manifest"], updates["input_manifest"], "input_id"
        )
    if "evidence" in updates:
        if not isinstance(updates["evidence"], list):
            raise ValueError("evidence update must be a list")
        session["evidence"] = merge_unique(
            session["evidence"], updates["evidence"], "evidence_id"
        )
    if "candidate_questions" in updates:
        if not isinstance(updates["candidate_questions"], list):
            raise ValueError("candidate_questions update must be a list")
        session["candidate_questions"] = updates["candidate_questions"]
    if "selection" in updates:
        session["selection"] = updates["selection"]
    if "decision_log" in updates:
        if not isinstance(updates["decision_log"], list):
            raise ValueError("decision_log update must be a list")
        session["decision_log"].extend(updates["decision_log"])
    if stage_id == "8_contract":
        session["status"] = "complete"
    else:
        session["status"] = f"stage_{int(stage_id.split('_', 1)[0]) + 1}"


def checkpoint_stage(run_dir: Path, envelope_path: Path) -> dict[str, Any]:
    state, session, _ = load_run(run_dir)
    expected_stage = rqs.next_stage_id(session)
    if expected_stage is None:
        raise ValueError("run has no pending stage")
    envelope = rqs.load_json(envelope_path)
    validate_envelope(envelope, state, expected_stage)
    apply_session_updates(session, expected_stage, envelope["session_updates"])
    trace = next(item for item in session["stage_trace"] if item["stage_id"] == expected_stage)
    trace["status"] = "complete"
    trace["output_summary"] = envelope["output_summary"].strip()
    trace["artifact_refs"] = envelope["artifact_refs"]

    destination = run_paths(run_dir)["stages"] / f"{expected_stage}.json"
    rqs.validate_session(
        session,
        rqs.load_json(rqs.DEFAULT_PROFILE),
        rqs.load_json(rqs.DEFAULT_PIPELINE),
        require_complete=False,
    )
    save_json(destination, envelope)
    save_json(run_paths(run_dir)["session"], session)
    state["checkpoints"].append(
        {
            "stage_id": expected_stage,
            "artifact_path": str(destination),
            "artifact_sha256": sha256_file(destination),
            "completed_at_utc": utc_now(),
        }
    )
    state["current_stage"] = rqs.next_stage_id(session)
    state["status"] = "awaiting_final_audit" if state["current_stage"] is None else "active"
    state["updated_at_utc"] = utc_now()
    save_json(run_paths(run_dir)["state"], state)
    return state


def record_failure(run_dir: Path, message: str) -> dict[str, Any]:
    state, session, _ = load_run(run_dir)
    state["failures"].append(
        {
            "stage_id": rqs.next_stage_id(session),
            "recorded_at_utc": utc_now(),
            "message": rqs.require_text(message, "message"),
        }
    )
    state["updated_at_utc"] = utc_now()
    save_json(run_paths(run_dir)["state"], state)
    return state


def append_unique(items: list[dict[str, Any]], item: dict[str, Any], field: str) -> None:
    value = item[field]
    if any(existing[field] == value for existing in items):
        raise ValueError(f"duplicate {field}: {value}")
    items.append(item)


def update_ledger(run_dir: Path, kind: str, item: dict[str, Any]) -> dict[str, Any]:
    _, _, ledger = load_run(run_dir)
    collection, id_field = {
        "query": ("queries", "query_id"),
        "source": ("source_decisions", "evidence_id"),
        "collision": ("collision_reviews", "candidate_id"),
    }[kind]
    append_unique(ledger[collection], item, id_field)
    ledger["updated_at_utc"] = utc_now()
    save_json(run_paths(run_dir)["ledger"], ledger)
    return ledger


def ledger_audit(session: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add(target: list[dict[str, str]], code: str, message: str) -> None:
        target.append({"code": code, "message": message})

    query_ids = {item["query_id"] for item in ledger["queries"]}
    if not query_ids:
        add(
            errors,
            "missing_query_log",
            "completed research requires at least one logged search query",
        )

    evidence_by_id = {item["evidence_id"]: item for item in session["evidence"]}
    evidence_ids = set(evidence_by_id)
    decisions = {item["evidence_id"]: item for item in ledger["source_decisions"]}
    online_evidence = {
        item["evidence_id"]
        for item in session["evidence"]
        if item["source_location"].startswith(("http://", "https://"))
    }
    for evidence_id in sorted(online_evidence):
        decision = decisions.get(evidence_id)
        if decision is None or decision.get("disposition") != "include":
            add(
                errors,
                "unlogged_online_evidence",
                f"{evidence_id} lacks an include source decision",
            )
            continue
        unknown_queries = set(decision.get("query_ids", [])) - query_ids
        if unknown_queries:
            add(
                errors,
                "unknown_query_reference",
                f"{evidence_id} references {sorted(unknown_queries)}",
            )
        if (
            decision.get("source_location")
            != evidence_by_id[evidence_id]["source_location"]
        ):
            add(
                errors,
                "source_location_mismatch",
                f"{evidence_id} ledger location differs from the session evidence",
            )

    if session.get("selection"):
        selected = [
            session["selection"]["primary_candidate_id"],
            *session["selection"]["backup_candidate_ids"],
        ]
        reviews = {
            item["candidate_id"]: item for item in ledger["collision_reviews"]
        }
        for candidate_id in selected:
            review = reviews.get(candidate_id)
            if review is None:
                add(
                    errors,
                    "missing_collision_review",
                    f"{candidate_id} lacks a collision review",
                )
                continue
            if not review.get("nonredundant_increment", "").strip():
                add(
                    errors,
                    "missing_nonredundant_increment",
                    f"{candidate_id} lacks a stated increment",
                )
            unknown_queries = set(review.get("query_ids", [])) - query_ids
            if unknown_queries:
                add(
                    errors,
                    "collision_unknown_query",
                    f"{candidate_id} references {sorted(unknown_queries)}",
                )
            unknown_evidence = (
                set(review.get("closest_evidence_ids", [])) - evidence_ids
            )
            if unknown_evidence:
                add(
                    errors,
                    "collision_unknown_evidence",
                    f"{candidate_id} references {sorted(unknown_evidence)}",
                )
            if review.get("disposition") == "reject":
                add(
                    errors,
                    "selected_collision_rejected",
                    f"{candidate_id} is selected but its collision review says reject",
                )

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "query_count": len(ledger["queries"]),
            "source_decision_count": len(ledger["source_decisions"]),
            "collision_review_count": len(ledger["collision_reviews"]),
            "online_evidence_count": len(online_evidence),
        },
    }


def audit_run(run_dir: Path, require_complete: bool) -> dict[str, Any]:
    state, session, ledger = load_run(run_dir)
    profile = rqs.load_json(rqs.DEFAULT_PROFILE)
    pipeline = rqs.load_json(rqs.DEFAULT_PIPELINE)
    structural_error = None
    try:
        rqs.validate_session(session, profile, pipeline, require_complete=require_complete)
    except ValueError as error:
        structural_error = str(error)
    semantic = (
        rqs.semantic_audit(session)
        if session.get("status") == "complete"
        else {"passed": not require_complete, "errors": [], "warnings": [], "metrics": {}}
    )
    evidence = ledger_audit(session, ledger)
    passed = structural_error is None and semantic["passed"] and (
        evidence["passed"] if require_complete else True
    )
    return {
        "run_id": state["run_id"],
        "passed": passed,
        "structural_error": structural_error,
        "semantic_audit": semantic,
        "evidence_ledger_audit": evidence,
    }


def finalize_run(run_dir: Path, result_path: Path | None) -> dict[str, Any]:
    state, session, ledger = load_run(run_dir)
    audit = audit_run(run_dir, require_complete=True)
    if not audit["passed"]:
        raise ValueError("final audit failed: " + json.dumps(audit, ensure_ascii=False))
    state["status"] = "complete"
    state["current_stage"] = None
    state["updated_at_utc"] = utc_now()
    save_json(run_paths(run_dir)["state"], state)

    artifacts = [
        run_paths(run_dir)["session"],
        run_paths(run_dir)["ledger"],
        *sorted(run_paths(run_dir)["stages"].glob("*.json")),
    ]
    manifest = {
        "manifest_version": "1.0",
        "run_id": state["run_id"],
        "mode": state["mode"],
        "engine": state["engine"],
        "completed_at_utc": utc_now(),
        "audit": audit,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path)} for path in artifacts
        ],
    }
    save_json(run_dir / "completion-manifest.json", manifest)
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(result_path, session)
        save_json(result_path.with_suffix(".evidence-ledger.json"), ledger)
        save_json(result_path.with_suffix(".run-manifest.json"), manifest)
    return manifest


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable runner for the shared research-question funnel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--mode", choices=rqs.MODES, required=True)
    init.add_argument("--run-id", required=True)
    init.add_argument("--run-dir", type=Path)
    init.add_argument("--input-manifest", type=Path)

    for command in ("next", "status", "audit"):
        item = subparsers.add_parser(command)
        item.add_argument("--run", required=True)
    audit = subparsers.choices["audit"]
    audit.add_argument("--complete", action="store_true")

    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint.add_argument("--run", required=True)
    checkpoint.add_argument("--envelope", type=Path, required=True)

    failure = subparsers.add_parser("fail")
    failure.add_argument("--run", required=True)
    failure.add_argument("--message", required=True)

    query = subparsers.add_parser("log-query")
    query.add_argument("--run", required=True)
    query.add_argument("--id", required=True)
    query.add_argument("--text", required=True)
    query.add_argument("--provider", required=True)
    query.add_argument("--scope", required=True)
    query.add_argument("--result-count", type=int, required=True)

    source = subparsers.add_parser("log-source")
    source.add_argument("--run", required=True)
    source.add_argument("--evidence-id", required=True)
    source.add_argument("--query-id", action="append", required=True)
    source.add_argument("--disposition", choices=("include", "exclude"), required=True)
    source.add_argument("--location", required=True)
    source.add_argument("--source-type", required=True)
    source.add_argument("--claim", required=True)
    source.add_argument("--reason", required=True)

    collision = subparsers.add_parser("log-collision")
    collision.add_argument("--run", required=True)
    collision.add_argument("--candidate-id", required=True)
    collision.add_argument("--query-id", action="append", required=True)
    collision.add_argument("--closest-evidence-id", action="append", required=True)
    collision.add_argument("--overlap", required=True)
    collision.add_argument("--increment", required=True)
    collision.add_argument("--disposition", choices=("keep", "narrow", "rewrite", "reject"), required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--run", required=True)
    finalize.add_argument("--result", type=Path)

    args = parser.parse_args()
    if args.command == "init":
        run_dir = args.run_dir or RUNS_ROOT / args.run_id
        state = initialize_run(
            args.run_id,
            args.mode,
            run_dir.resolve(),
            parse_input_manifest(args.input_manifest),
        )
        print_json({"run_dir": str(run_dir.resolve()), **state})
        return 0

    run_dir = resolve_run(args.run)
    if args.command == "next":
        print_json({"packet": str(build_packet(run_dir))})
    elif args.command == "status":
        state, session, ledger = load_run(run_dir)
        print_json(
            {
                "state": state,
                "session_status": session["status"],
                "next_stage": rqs.next_stage_id(session),
                "evidence_count": len(session["evidence"]),
                "candidate_count": len(session["candidate_questions"]),
                "ledger_counts": {
                    "queries": len(ledger["queries"]),
                    "sources": len(ledger["source_decisions"]),
                    "collisions": len(ledger["collision_reviews"]),
                },
            }
        )
    elif args.command == "checkpoint":
        print_json(checkpoint_stage(run_dir, args.envelope))
    elif args.command == "fail":
        print_json(record_failure(run_dir, args.message))
    elif args.command == "log-query":
        if args.result_count < 0:
            parser.error("--result-count cannot be negative")
        print_json(
            update_ledger(
                run_dir,
                "query",
                {
                    "query_id": args.id,
                    "text": args.text,
                    "provider": args.provider,
                    "scope": args.scope,
                    "executed_at_utc": utc_now(),
                    "result_count": args.result_count,
                },
            )
        )
    elif args.command == "log-source":
        print_json(
            update_ledger(
                run_dir,
                "source",
                {
                    "evidence_id": args.evidence_id,
                    "query_ids": list(dict.fromkeys(args.query_id)),
                    "disposition": args.disposition,
                    "source_location": args.location,
                    "source_type": args.source_type,
                    "claim_supported": args.claim,
                    "reason": args.reason,
                    "checked_at_utc": utc_now(),
                },
            )
        )
    elif args.command == "log-collision":
        print_json(
            update_ledger(
                run_dir,
                "collision",
                {
                    "candidate_id": args.candidate_id,
                    "query_ids": list(dict.fromkeys(args.query_id)),
                    "closest_evidence_ids": list(dict.fromkeys(args.closest_evidence_id)),
                    "overlap": args.overlap,
                    "nonredundant_increment": args.increment,
                    "disposition": args.disposition,
                    "checked_at_utc": utc_now(),
                },
            )
        )
    elif args.command == "audit":
        result = audit_run(run_dir, args.complete)
        print_json(result)
        return int(not result["passed"])
    elif args.command == "finalize":
        print_json(finalize_run(run_dir, args.result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
