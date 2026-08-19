from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "cases"
PROMPTS_DIR = ROOT / "prompts"
SESSIONS_DIR = ROOT / "sessions"
RESULTS_DIR = ROOT / "results"
CONDITION_FILES = {
    "A": "baseline.md",
    "B": "tool-chain.md",
    "C": "discovery-funnel.md",
}
CRITICALITY_RANK = {"distractor": 0, "supporting": 1, "critical": 2}


def load_cases() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case_id = case.get("case_id")
        if case_id in cases:
            raise ValueError(f"duplicate case_id: {case_id}")
        case["_path"] = str(path)
        cases[case_id] = case
    return cases


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "domain": case["domain"],
        "title": case["title"],
        "brief": case["brief"],
        "decision_deadline": case["decision"]["deadline"],
        "options": case["decision"]["options"],
        "question_budget": case.get("question_budget", 5),
    }


def normalize(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def answer_question(
    case: dict[str, Any], question: str, revealed_ids: set[str]
) -> tuple[str | None, str]:
    normalized_question = normalize(question)
    matches: list[tuple[int, int, str, str]] = []
    for fact in case["oracle_facts"]:
        if fact["id"] in revealed_ids:
            continue
        matched_lengths = [
            len(normalize(trigger))
            for trigger in fact["triggers"]
            if normalize(trigger) and normalize(trigger) in normalized_question
        ]
        if matched_lengths:
            matches.append(
                (
                    sum(matched_lengths),
                    CRITICALITY_RANK[fact["criticality"]],
                    fact["id"],
                    fact["answer"],
                )
            )
    if not matches:
        return None, "现有事实表无法回答这个问题。请把问题缩小到可观察的对象、分组、时间、流程或指标。"
    _, _, fact_id, answer = max(matches)
    return fact_id, answer


def validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "case_id",
        "domain",
        "title",
        "brief",
        "decision",
        "hypotheses",
        "oracle_facts",
        "key_unknowns",
        "utility",
        "leakage_terms",
    }
    missing = required - set(case)
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
        return errors

    option_ids = [option["id"] for option in case["decision"].get("options", [])]
    if len(option_ids) < 2 or len(option_ids) != len(set(option_ids)):
        errors.append("decision options must contain at least two unique ids")

    scores = case["utility"].get("option_scores", {})
    if set(scores) != set(option_ids):
        errors.append("utility.option_scores must cover exactly all option ids")
    if scores:
        max_score = max(scores.values())
        best = [option_id for option_id, score in scores.items() if score == max_score]
        if len(best) != 1:
            errors.append("utility must have one unique best option")
        elif case["utility"].get("best_option") != best[0]:
            errors.append("utility.best_option does not match maximum score")

    fact_ids = [fact.get("id") for fact in case["oracle_facts"]]
    if len(fact_ids) != len(set(fact_ids)):
        errors.append("oracle fact ids must be unique")
    for fact in case["oracle_facts"]:
        if fact.get("criticality") not in CRITICALITY_RANK:
            errors.append(f"fact {fact.get('id')} has invalid criticality")
        if not fact.get("triggers"):
            errors.append(f"fact {fact.get('id')} has no triggers")
        if not fact.get("answer"):
            errors.append(f"fact {fact.get('id')} has no answer")

    for unknown in case["key_unknowns"]:
        if unknown.get("weight", 0) <= 0:
            errors.append(f"key unknown {unknown.get('id')} must have positive weight")
        for fact_id in unknown.get("fact_ids", []):
            if fact_id not in fact_ids:
                errors.append(
                    f"key unknown {unknown.get('id')} references missing fact {fact_id}"
                )

    public_text = normalize(case["title"] + case["brief"])
    for term in case["leakage_terms"]:
        if normalize(term) in public_text:
            errors.append(f"hidden leakage term appears in public text: {term}")

    if case.get("question_budget", 5) != 5:
        errors.append("v0.1 cases must use a five-question budget")
    return errors


def validate_all(cases: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(cases) != 12:
        errors.append(f"expected 12 cases, found {len(cases)}")
    domain_counts: dict[str, int] = {}
    for case_id, case in cases.items():
        domain_counts[case["domain"]] = domain_counts.get(case["domain"], 0) + 1
        errors.extend(f"{case_id}: {error}" for error in validate_case(case))
    expected_domains = {"product": 3, "operations": 3, "research": 3, "project": 3}
    if domain_counts != expected_domains:
        errors.append(f"unexpected domain distribution: {domain_counts}")
    for condition, filename in CONDITION_FILES.items():
        if not (PROMPTS_DIR / filename).exists():
            errors.append(f"missing prompt for condition {condition}: {filename}")
    return errors


def score_session(case: dict[str, Any], session: dict[str, Any]) -> dict[str, float]:
    scores = case["utility"]["option_scores"]
    pre_utility = float(scores[session["pre_decision"]])
    post_utility = float(scores[session["post_decision"]])
    questions = session.get("questions", [])
    revealed = {item["fact_id"] for item in questions if item.get("fact_id")}

    total_key_weight = sum(item["weight"] for item in case["key_unknowns"])
    hit_key_weight = sum(
        item["weight"]
        for item in case["key_unknowns"]
        if any(fact_id in revealed for fact_id in item["fact_ids"])
    )
    critical_ids = {
        fact["id"]
        for fact in case["oracle_facts"]
        if fact["criticality"] == "critical"
    }
    improvement = post_utility - pre_utility
    question_count = len(questions)
    return {
        "pre_utility": pre_utility,
        "post_utility": post_utility,
        "decision_improvement": improvement,
        "normalized_post_utility": post_utility / max(scores.values()),
        "key_unknown_recall": hit_key_weight / total_key_weight,
        "critical_fact_hit_rate": len(revealed & critical_ids) / len(critical_ids),
        "information_efficiency": improvement / question_count if question_count else 0.0,
        "questions_used": float(question_count),
    }


def build_schedule(
    cases: dict[str, dict[str, Any]], randomization_seed: int = 20260819
) -> list[dict[str, Any]]:
    """Create a reproducible, balanced 12 x 3 x 3 blind-run schedule."""
    runs = [
        {
            "case_id": case_id,
            "domain": case["domain"],
            "condition": condition,
            "model_seed": model_seed,
        }
        for case_id, case in sorted(cases.items())
        for condition in sorted(CONDITION_FILES)
        for model_seed in (1, 2, 3)
    ]
    random.Random(randomization_seed).shuffle(runs)
    for index, run in enumerate(runs, start=1):
        run["run_order"] = index
        run["blind_run_id"] = f"QD-{index:03d}"
    return runs


def choose_option(case: dict[str, Any], label: str) -> str:
    options = {option["id"] for option in case["decision"]["options"]}
    while True:
        value = input(label).strip()
        if value in options:
            return value
        print(f"无效选项，请输入：{', '.join(sorted(options))}")


def run_session(case: dict[str, Any], condition: str) -> Path:
    prompt = (PROMPTS_DIR / CONDITION_FILES[condition]).read_text(encoding="utf-8")
    payload = public_case(case)
    print(prompt)
    print("\n## 公开案例\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n请在全新模型对话中使用以上内容，并把模型输出复制回来。\n")

    pre_decision = choose_option(case, "PRE_DECISION option_id> ")
    revealed: set[str] = set()
    questions: list[dict[str, Any]] = []
    budget = payload["question_budget"]
    for index in range(1, budget + 1):
        question = input(f"QUESTION {index}/{budget}（输入 DECIDE 提前结束）> ").strip()
        if question.upper() == "DECIDE":
            break
        fact_id, answer = answer_question(case, question, revealed)
        if fact_id:
            revealed.add(fact_id)
        questions.append(
            {
                "question": question,
                "fact_id": fact_id,
                "oracle_answer": answer,
                "manual_annotations": {
                    "decision_changing": None,
                    "discriminative": None,
                    "unsupported_premise": None,
                    "answerable": None,
                },
            }
        )
        print(f"ORACLE: {answer}")

    post_decision = choose_option(case, "DECISION option_id> ")
    rationale = input("RATIONALE> ").strip()
    now = datetime.now(timezone.utc)
    session = {
        "benchmark_version": "0.1",
        "case_id": case["case_id"],
        "condition": condition,
        "started_at_utc": now.isoformat(),
        "pre_decision": pre_decision,
        "questions": questions,
        "post_decision": post_decision,
        "rationale": rationale,
        "manual_review": {
            "reviewer_id": None,
            "false_balance": None,
            "sensitive_information_risk": None,
            "user_burden_1_to_5": None,
            "oracle_errors": [],
            "notes": None,
        },
    }
    session["automatic_metrics"] = score_session(case, session)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = SESSIONS_DIR / f"{case['case_id']}-{condition}-{stamp}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n自动指标：")
    print(json.dumps(session["automatic_metrics"], ensure_ascii=False, indent=2))
    print(f"会话已保存：{path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Question Discovery Benchmark v0.1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("case_id")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("case_id")
    run_parser.add_argument("--condition", choices=CONDITION_FILES, required=True)
    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("session_file", type=Path)
    schedule_parser = subparsers.add_parser("schedule")
    schedule_parser.add_argument("--seed", type=int, default=20260819)
    schedule_parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_cases()
    if args.command == "validate":
        errors = validate_all(cases)
        if errors:
            print("Validation failed:")
            for error in errors:
                print(f"- {error}")
            return 1
        print("Validation passed: 12 cases, 3 per domain, no structural leaks found.")
        return 0
    if args.command == "list":
        for case in cases.values():
            print(f"{case['case_id']}\t{case['domain']}\t{case['title']}")
        return 0
    if args.command == "schedule":
        schedule = {
            "benchmark_version": "0.1",
            "randomization_seed": args.seed,
            "total_runs": len(cases) * len(CONDITION_FILES) * 3,
            "runs": build_schedule(cases, args.seed),
        }
        rendered = json.dumps(schedule, ensure_ascii=False, indent=2)
        if args.output:
            output = args.output
            if not output.is_absolute():
                output = ROOT / output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Schedule saved: {output} ({schedule['total_runs']} runs)")
        else:
            print(rendered)
        return 0
    if args.command in {"show", "run"} and args.case_id not in cases:
        parser.error(f"unknown case_id: {args.case_id}")
    if args.command == "show":
        print(json.dumps(public_case(cases[args.case_id]), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        run_session(cases[args.case_id], args.condition)
        return 0
    if args.command == "score":
        session = json.loads(args.session_file.read_text(encoding="utf-8"))
        case = cases[session["case_id"]]
        print(json.dumps(score_session(case, session), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
