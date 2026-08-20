from __future__ import annotations

import argparse
import json
import random
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "cases"
PROMPTS_DIR = ROOT / "prompts"
SESSIONS_DIR = ROOT / "sessions"
RESULTS_DIR = ROOT / "results"
DEFAULT_CONFIG_PATH = ROOT / "model-config.local.json"
CONDITION_FILES = {
    "A": "baseline.md",
    "B": "tool-chain.md",
    "C": "discovery-funnel.md",
}
CRITICALITY_RANK = {"distractor": 0, "supporting": 1, "critical": 2}
RUN_MODES = {"api", "direct"}
BENCHMARK_VERSION = "0.2"
EXPLANATION_STATE_PROMPT = "explicit-explanation-state.md"
EVIDENCE_MENU_PROMPT = "evidence-menu.md"
EVIDENCE_CONTRACT_PROMPT = "evidence-contract.md"


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


def public_option_map(case: dict[str, Any]) -> dict[str, str]:
    if "option_rotation" in case:
        rotation = int(case["option_rotation"]) % 4
    else:
        domain_offsets = {"product": 0, "operations": 1, "research": 2, "project": 3}
        case_number = int(case["case_id"].rsplit("-", 1)[1])
        rotation = (domain_offsets[case["domain"]] + case_number - 1) % 4
    internal_ids = [option["id"] for option in case["decision"]["options"]]
    rotated_ids = internal_ids[rotation:] + internal_ids[:rotation]
    return {
        f"option_{chr(ord('a') + index)}": internal_id
        for index, internal_id in enumerate(rotated_ids)
    }


def public_options(case: dict[str, Any]) -> list[dict[str, str]]:
    mapping = public_option_map(case)
    internal_options = {option["id"]: option for option in case["decision"]["options"]}
    return [
        {
            "id": public_id,
            "label": internal_options[internal_id]["public_label"],
        }
        for public_id, internal_id in mapping.items()
    ]


def resolve_internal_option(case: dict[str, Any], option_id: str) -> str:
    mapping = public_option_map(case)
    if option_id in mapping:
        return mapping[option_id]
    internal_ids = {option["id"] for option in case["decision"]["options"]}
    if option_id in internal_ids:
        return option_id
    raise ValueError(f"unknown option_id: {option_id}")


def best_public_option(case: dict[str, Any]) -> str:
    best_internal = case["utility"]["best_option"]
    return next(
        public_id
        for public_id, internal_id in public_option_map(case).items()
        if internal_id == best_internal
    )


def public_case(
    case: dict[str, Any], include_evidence_catalog: bool = False
) -> dict[str, Any]:
    payload = {
        "case_id": case.get("public_case_id", case["case_id"]),
        "domain": case["domain"],
        "title": case["title"],
        "brief": case["brief"],
        "decision_deadline": case["decision"]["deadline"],
        "options": public_options(case),
        "question_budget": case.get("question_budget", 5),
    }
    if include_evidence_catalog:
        payload["evidence_catalog"] = case["evidence_catalog"]
    if case.get("evidence_capabilities"):
        payload["evidence_capabilities"] = case["evidence_capabilities"]
    return payload


def evidence_catalog_map(case: dict[str, Any]) -> dict[str, str]:
    return {item["id"]: item["question"] for item in case.get("evidence_catalog", [])}


def answer_evidence_query(
    case: dict[str, Any], evidence_id: str, revealed_ids: set[str]
) -> tuple[str | None, str]:
    catalog = evidence_catalog_map(case)
    if evidence_id not in catalog:
        raise ValueError(f"unknown evidence_id: {evidence_id}")
    matches = [
        fact
        for fact in case["oracle_facts"]
        if fact.get("evidence_id") == evidence_id and fact["id"] not in revealed_ids
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{case['case_id']}: evidence_id {evidence_id} must map to one unrevealed fact"
        )
    fact = matches[0]
    return fact["id"], fact["answer"]


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
    if len(option_ids) != 4 or len(option_ids) != len(set(option_ids)):
        errors.append("v0.2 decision options must contain exactly four unique ids")
    for option in case["decision"].get("options", []):
        if not option.get("public_label"):
            errors.append(f"option {option.get('id')} has no public_label")

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

    public_text = normalize(
        case["title"]
        + case["brief"]
        + " ".join(option.get("public_label", "") for option in case["decision"]["options"])
    )
    for term in case["leakage_terms"]:
        if normalize(term) in public_text:
            errors.append(f"hidden leakage term appears in public text: {term}")

    question_budget = case.get("question_budget", 5)
    if (
        not isinstance(question_budget, int)
        or isinstance(question_budget, bool)
        or not 1 <= question_budget <= 5
    ):
        errors.append("benchmark question budget must be an integer from 1 to 5")
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
    best_position_counts = {f"option_{letter}": 0 for letter in "abcd"}
    for case in cases.values():
        best_position_counts[best_public_option(case)] += 1
    if best_position_counts != {key: 3 for key in best_position_counts}:
        errors.append(f"best public option positions are not balanced: {best_position_counts}")
    for condition, filename in CONDITION_FILES.items():
        if not (PROMPTS_DIR / filename).exists():
            errors.append(f"missing prompt for condition {condition}: {filename}")
    return errors


def validate_probabilities(
    case: dict[str, Any], probabilities: dict[str, Any], field: str
) -> dict[str, float]:
    expected = set(public_option_map(case))
    if set(probabilities) != expected:
        raise ValueError(
            f"{field} 必须且只能包含：{', '.join(sorted(expected))}"
        )
    try:
        converted = {key: float(value) for key, value in probabilities.items()}
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} 的概率必须是数字") from error
    if any(value < 0 or value > 1 for value in converted.values()):
        raise ValueError(f"{field} 的每个概率必须在 0 到 1 之间")
    if abs(sum(converted.values()) - 1.0) > 0.02:
        raise ValueError(f"{field} 的概率之和必须约等于 1")
    total = sum(converted.values())
    return {key: round(value / total, 12) for key, value in converted.items()}


def parse_probability_field(
    case: dict[str, Any], text: str, field: str
) -> dict[str, float] | None:
    raw = parse_protocol_field(text, field)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} 不是有效 JSON 对象：{raw[:200]}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{field} 必须是 JSON 对象")
    return validate_probabilities(case, payload, field)


def probability_quality(case: dict[str, Any], probabilities: dict[str, float]) -> float:
    best = best_public_option(case)
    brier = sum(
        (probability - (1.0 if option_id == best else 0.0)) ** 2
        for option_id, probability in probabilities.items()
    )
    return 1.0 - brier / 2.0


def score_session(case: dict[str, Any], session: dict[str, Any]) -> dict[str, float]:
    scores = case["utility"]["option_scores"]
    pre_internal = resolve_internal_option(case, session["pre_decision"])
    post_internal = resolve_internal_option(case, session["post_decision"])
    pre_utility = float(scores[pre_internal])
    post_utility = float(scores[post_internal])
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
    metrics = {
        "pre_utility": pre_utility,
        "post_utility": post_utility,
        "decision_improvement": improvement,
        "normalized_post_utility": post_utility / max(scores.values()),
        "key_unknown_recall": hit_key_weight / total_key_weight,
        "critical_fact_hit_rate": len(revealed & critical_ids) / len(critical_ids),
        "information_efficiency": improvement / question_count if question_count else 0.0,
        "questions_used": float(question_count),
        "no_fact_answer_rate": (
            sum(1 for item in questions if not item.get("fact_id")) / question_count
            if question_count
            else 0.0
        ),
        "protocol_deviation_count": float(
            len(session.get("protocol_deviations") or [])
        ),
        "oracle_match_disagreement_rate": (
            sum(
                1
                for item in questions
                if item.get("oracle_match_disagreement") is True
            )
            / question_count
            if question_count
            else 0.0
        ),
    }
    if any(item.get("evidence_id") for item in questions):
        fact_criticality = {
            fact["id"]: fact["criticality"] for fact in case["oracle_facts"]
        }
        metrics.update(
            {
                "catalog_evidence_selection_count": float(question_count),
                "first_selection_critical": float(
                    bool(questions)
                    and fact_criticality.get(questions[0].get("fact_id")) == "critical"
                ),
                "supporting_evidence_selection_rate": (
                    sum(
                        fact_criticality.get(item.get("fact_id")) == "supporting"
                        for item in questions
                    )
                    / question_count
                    if question_count
                    else 0.0
                ),
                "distractor_evidence_selection_rate": (
                    sum(
                        fact_criticality.get(item.get("fact_id")) == "distractor"
                        for item in questions
                    )
                    / question_count
                    if question_count
                    else 0.0
                ),
            }
        )
    if session.get("pre_probabilities") and session.get("post_probabilities"):
        pre_probabilities = validate_probabilities(
            case, session["pre_probabilities"], "pre_probabilities"
        )
        post_probabilities = validate_probabilities(
            case, session["post_probabilities"], "post_probabilities"
        )
        pre_quality = probability_quality(case, pre_probabilities)
        post_quality = probability_quality(case, post_probabilities)
        best = best_public_option(case)
        metrics.update(
            {
                "pre_probability_quality": pre_quality,
                "post_probability_quality": post_quality,
                "probability_quality_improvement": post_quality - pre_quality,
                "best_option_probability_change": (
                    post_probabilities[best] - pre_probabilities[best]
                ),
                "probability_information_efficiency": (
                    (post_quality - pre_quality) / question_count
                    if question_count
                    else 0.0
                ),
                "pre_choice_probability_consistent": float(
                    pre_probabilities[session["pre_decision"]]
                    == max(pre_probabilities.values())
                ),
                "post_choice_probability_consistent": float(
                    post_probabilities[session["post_decision"]]
                    == max(post_probabilities.values())
                ),
            }
        )
    return metrics


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


def build_calibration_schedule(
    cases: dict[str, dict[str, Any]], randomization_seed: int = 20260819
) -> list[dict[str, Any]]:
    """Create a 12-run schedule balanced within every domain across A/B/C."""
    domain_offsets = {"product": 0, "operations": 1, "research": 2, "project": 0}
    conditions = tuple(sorted(CONDITION_FILES))
    runs: list[dict[str, Any]] = []
    for case_id, case in sorted(cases.items()):
        case_number = int(case_id.rsplit("-", 1)[1])
        condition = conditions[
            (case_number - 1 + domain_offsets[case["domain"]]) % len(conditions)
        ]
        runs.append(
            {
                "case_id": case_id,
                "domain": case["domain"],
                "condition": condition,
                "model_seed": case_number,
            }
        )
    random.Random(randomization_seed).shuffle(runs)
    for index, run in enumerate(runs, start=1):
        run["run_order"] = index
        run["calibration_run_id"] = f"CAL2-{index:03d}"
    return runs


def load_model_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"找不到模型配置文件：{path}。请复制 model-config.example.json "
            "为 model-config.local.json 并填写 url、api_key、model_name。"
        )
    config = json.loads(path.read_text(encoding="utf-8"))
    missing = [name for name in ("url", "model_name") if not config.get(name)]
    if missing:
        raise ValueError(f"模型配置缺少必填项：{', '.join(missing)}")
    return config


def completion_endpoint(url: str) -> str:
    stripped = url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    return f"{stripped}/chat/completions"


def api_chat_completion(
    config: dict[str, Any], messages: list[dict[str, str]], model_seed: int | None
) -> str:
    body: dict[str, Any] = {
        "model": config["model_name"],
        "messages": messages,
        "temperature": float(config.get("temperature", 0.2)),
    }
    if model_seed is not None and config.get("send_seed", True):
        body["seed"] = model_seed
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    request = urllib.request.Request(
        completion_endpoint(config["url"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    max_retries = max(0, int(config.get("api_max_retries", 1)))
    payload: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=float(config.get("timeout_seconds", 120))
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            retryable = error.code == 429 or 500 <= error.code < 600
            if retryable and attempt < max_retries:
                print(
                    f"API_RETRY: HTTP {error.code}，"
                    f"正在进行第 {attempt + 1}/{max_retries} 次重试。"
                )
                continue
            raise RuntimeError(
                f"模型 API 返回 HTTP {error.code}: {detail[:500]}"
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < max_retries:
                print(
                    "API_RETRY: 连接或读取超时，"
                    f"正在进行第 {attempt + 1}/{max_retries} 次重试。"
                )
                continue
            reason = getattr(error, "reason", str(error))
            raise RuntimeError(f"无法连接模型 API：{reason}") from error
    if payload is None:
        raise RuntimeError("模型 API 重试后没有返回响应")

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("模型 API 响应不包含 choices[0].message.content") from error
    if isinstance(content, list):
        content = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型 API 返回了空文本")
    return content.strip()


def oracle_api_config(config: dict[str, Any]) -> dict[str, Any]:
    oracle_config = dict(config)
    oracle_config["url"] = config.get("oracle_url") or config["url"]
    oracle_config["api_key"] = config.get("oracle_api_key") or config.get("api_key", "")
    oracle_config["model_name"] = (
        config.get("oracle_model_name") or config["model_name"]
    )
    oracle_config["temperature"] = 0
    return oracle_config


def semantic_answer_question(
    case: dict[str, Any],
    question: str,
    revealed_ids: set[str],
    config: dict[str, Any],
) -> tuple[str | None, str, str]:
    candidates = [
        {"fact_id": fact["id"], "fact": fact["answer"]}
        for fact in case["oracle_facts"]
        if fact["id"] not in revealed_ids
    ]
    if not candidates:
        return None, "现有事实表没有尚未揭示的相关事实。", "ORACLE_FACT_ID: NONE"
    messages = [
        {
            "role": "system",
            "content": (
                "你是与受测对话隔离的事实选择器。只能判断候选事实是否直接回答问题，"
                "不能补充、推断或改写事实。相关但没有回答所问对象、比较、时间或指标的"
                "事实不能选择。若问题包含多个子句，选择最直接回答主要决策分叉的一条。"
                "严格只输出 ORACLE_FACT_ID: <fact_id> 或 ORACLE_FACT_ID: NONE。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "candidate_facts": candidates},
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]
    raw = api_chat_completion(oracle_api_config(config), messages, model_seed=0)
    selected = parse_protocol_field(raw, "ORACLE_FACT_ID")
    valid_ids = {candidate["fact_id"] for candidate in candidates}
    if selected is None:
        raise ValueError(f"语义 Oracle 未返回 ORACLE_FACT_ID：{raw[:300]}")
    if selected.upper() == "NONE":
        return (
            None,
            "现有事实表无法回答这个问题。请把问题缩小到可观察的对象、分组、时间、流程或指标。",
            raw,
        )
    if selected not in valid_ids:
        raise ValueError(f"语义 Oracle 返回无效或已揭示 fact_id：{selected!r}")
    fact = next(fact for fact in case["oracle_facts"] if fact["id"] == selected)
    return selected, fact["answer"], raw


def parse_protocol_field(text: str, field: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(field)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def uses_explanation_state(condition: str, prompt_file: str | None) -> bool:
    return condition in {"E", "F"} or prompt_file in {
        EXPLANATION_STATE_PROMPT,
        EVIDENCE_CONTRACT_PROMPT,
    }


def uses_evidence_catalog(condition: str, prompt_file: str | None) -> bool:
    return condition in {"Q", "F"} or prompt_file in {
        EVIDENCE_MENU_PROMPT,
        EVIDENCE_CONTRACT_PROMPT,
    }


def uses_catalog_plan(condition: str, prompt_file: str | None) -> bool:
    return condition == "F" or prompt_file == EVIDENCE_CONTRACT_PROMPT


def parse_explanation_plan(
    case: dict[str, Any], text: str
) -> list[dict[str, str]]:
    raw = parse_protocol_field(text, "EXPLANATIONS")
    if raw is None:
        raise ValueError("缺少 EXPLANATIONS")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("EXPLANATIONS 不是有效 JSON 数组") from error
    if not isinstance(payload, list) or len(payload) != 3:
        raise ValueError("EXPLANATIONS 必须恰好包含 3 个解释")
    expected_ids = {"H1", "H2", "H3"}
    normalized: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("每个解释必须是 JSON 对象")
        explanation_id = item.get("id")
        explanation = item.get("explanation")
        evidence_target = item.get("evidence_target")
        action = item.get("action")
        if explanation_id not in expected_ids:
            raise ValueError("解释 id 必须是 H1、H2 或 H3")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError(f"{explanation_id} 缺少 explanation")
        if not isinstance(evidence_target, str) or not evidence_target.strip():
            raise ValueError(f"{explanation_id} 缺少 evidence_target")
        if not isinstance(action, str):
            raise ValueError(f"{explanation_id} 缺少 action")
        normalized.append(
            {
                "id": explanation_id,
                "explanation": explanation.strip(),
                "evidence_target": evidence_target.strip(),
                "action": validate_option(case, action, f"{explanation_id}.action"),
            }
        )
    ids = [item["id"] for item in normalized]
    if set(ids) != expected_ids or len(ids) != len(set(ids)):
        raise ValueError("解释 id 必须恰好各出现一次：H1、H2、H3")
    actions = [item["action"] for item in normalized]
    if len(actions) != len(set(actions)):
        raise ValueError("三个解释必须指向三个不同的最佳行动")
    return sorted(normalized, key=lambda item: item["id"])


def parse_catalog_explanation_plan(
    case: dict[str, Any], text: str
) -> list[dict[str, str]]:
    raw = parse_protocol_field(text, "EXPLANATIONS")
    if raw is None:
        raise ValueError("缺少 EXPLANATIONS")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("EXPLANATIONS 不是有效 JSON 数组") from error
    if not isinstance(payload, list) or len(payload) != 3:
        raise ValueError("EXPLANATIONS 必须恰好包含 3 个解释")
    expected_ids = {"H1", "H2", "H3"}
    valid_evidence = set(evidence_catalog_map(case))
    normalized: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("每个解释必须是 JSON 对象")
        explanation_id = item.get("id")
        explanation = item.get("explanation")
        evidence_id = item.get("evidence_id")
        action = item.get("action")
        if explanation_id not in expected_ids:
            raise ValueError("解释 id 必须是 H1、H2 或 H3")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError(f"{explanation_id} 缺少 explanation")
        if evidence_id not in valid_evidence:
            raise ValueError(
                f"{explanation_id}.evidence_id 必须是：{', '.join(sorted(valid_evidence))}"
            )
        if not isinstance(action, str):
            raise ValueError(f"{explanation_id} 缺少 action")
        normalized.append(
            {
                "id": explanation_id,
                "explanation": explanation.strip(),
                "evidence_id": evidence_id,
                "action": validate_option(case, action, f"{explanation_id}.action"),
            }
        )
    ids = [item["id"] for item in normalized]
    actions = [item["action"] for item in normalized]
    evidence_ids = [item["evidence_id"] for item in normalized]
    if set(ids) != expected_ids or len(ids) != len(set(ids)):
        raise ValueError("解释 id 必须恰好各出现一次：H1、H2、H3")
    if len(actions) != len(set(actions)):
        raise ValueError("三个解释必须指向三个不同的最佳行动")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("三个解释必须绑定三个不同的 evidence_id")
    return sorted(normalized, key=lambda item: item["id"])


def parse_evidence_selection(
    text: str, valid_evidence: set[str], attempted_evidence: set[str]
) -> str:
    evidence_id = parse_protocol_field(text, "EVIDENCE_ID")
    if evidence_id is None:
        raise ValueError("必须输出 EVIDENCE_ID")
    if evidence_id not in valid_evidence:
        raise ValueError(f"EVIDENCE_ID 必须是：{', '.join(sorted(valid_evidence))}")
    if evidence_id in attempted_evidence:
        raise ValueError(f"EVIDENCE_ID {evidence_id} 已尝试，不得复用")
    return evidence_id


def parse_targeted_question(
    text: str, valid_targets: set[str], attempted_targets: set[str]
) -> tuple[str, str]:
    target = parse_protocol_field(text, "TARGET")
    question = parse_protocol_field(text, "QUESTION")
    if target is None or question is None:
        raise ValueError("必须同时输出 TARGET 和 QUESTION")
    if target not in valid_targets:
        raise ValueError(f"TARGET 必须是：{', '.join(sorted(valid_targets))}")
    if target in attempted_targets:
        raise ValueError(f"TARGET {target} 已尝试，不得复用")
    return target, question


def validate_option(case: dict[str, Any], option_id: str, field: str) -> str:
    options = set(public_option_map(case))
    if option_id not in options:
        raise ValueError(
            f"模型的 {field} 不是有效 option_id：{option_id!r}；"
            f"有效值为 {', '.join(sorted(options))}"
        )
    return option_id


def choose_run_mode() -> str:
    print("请选择本次运行模式：")
    print("1. API 自动运行")
    print("2. 由当前 Codex 对话直接处理（人工交互）")
    while True:
        choice = input("模式 [1/2]> ").strip().lower()
        if choice in {"1", "api"}:
            return "api"
        if choice in {"2", "direct"}:
            return "direct"
        print("请输入 1 或 2。")


def choose_option(case: dict[str, Any], label: str) -> str:
    options = set(public_option_map(case))
    while True:
        value = input(label).strip()
        if value in options:
            return value
        print(f"无效选项，请输入：{', '.join(sorted(options))}")


def choose_probabilities(case: dict[str, Any], label: str) -> dict[str, float]:
    while True:
        raw = input(label).strip()
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("概率必须是 JSON 对象")
            return validate_probabilities(case, payload, label.strip())
        except (json.JSONDecodeError, ValueError) as error:
            print(f"概率格式无效：{error}")


def choose_explanation_plan(
    case: dict[str, Any], catalog_plan: bool = False
) -> list[dict[str, str]]:
    while True:
        raw = input("EXPLANATIONS JSON 数组> ").strip()
        if not raw.upper().startswith("EXPLANATIONS:"):
            raw = f"EXPLANATIONS: {raw}"
        try:
            return (
                parse_catalog_explanation_plan(case, raw)
                if catalog_plan
                else parse_explanation_plan(case, raw)
            )
        except ValueError as error:
            print(f"解释计划格式无效：{error}")


def save_session(case: dict[str, Any], session: dict[str, Any]) -> Path:
    session["automatic_metrics"] = score_session(case, session)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.fromisoformat(session["started_at_utc"])
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = SESSIONS_DIR / (
        f"{case['case_id']}-{session['condition']}-{session['mode']}-{stamp}.json"
    )
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n自动指标：")
    print(json.dumps(session["automatic_metrics"], ensure_ascii=False, indent=2))
    print(f"会话已保存：{path}")
    return path


def run_direct_session(
    case: dict[str, Any],
    condition: str,
    prompt_file: str | None = None,
    benchmark_version: str | None = None,
) -> Path:
    stateful = uses_explanation_state(condition, prompt_file)
    catalog_mode = uses_evidence_catalog(condition, prompt_file)
    catalog_plan = uses_catalog_plan(condition, prompt_file)
    prompt = (PROMPTS_DIR / (prompt_file or CONDITION_FILES[condition])).read_text(
        encoding="utf-8"
    )
    payload = public_case(case, include_evidence_catalog=catalog_mode)
    print(prompt)
    print("\n## 公开案例\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n请在全新模型对话中使用以上内容，并把模型输出复制回来。\n")

    pre_decision = choose_option(case, "PRE_DECISION option_id> ")
    pre_probabilities = choose_probabilities(
        case, 'PRE_PROBABILITIES JSON（例如 {"option_a":0.25,...}）> '
    )
    explanation_plan = (
        choose_explanation_plan(case, catalog_plan=catalog_plan) if stateful else []
    )
    valid_targets = {item["id"] for item in explanation_plan}
    attempted_targets: dict[str, str] = {}
    catalog = evidence_catalog_map(case) if catalog_mode else {}
    attempted_evidence: set[str] = set()
    revealed: set[str] = set()
    questions: list[dict[str, Any]] = []
    budget = payload["question_budget"]
    for index in range(1, budget + 1):
        target: str | None = None
        evidence_id: str | None = None
        if stateful:
            remaining = valid_targets - set(attempted_targets)
            if not remaining:
                break
            while True:
                target = input(
                    f"TARGET {index}/{budget}（{','.join(sorted(remaining))}；"
                    "输入 DECIDE 提前结束）> "
                ).strip()
                if target.upper() == "DECIDE":
                    break
                if target in remaining:
                    break
                print(f"无效或已尝试目标，请输入：{', '.join(sorted(remaining))}")
            if target.upper() == "DECIDE":
                break
            if catalog_mode:
                evidence_id = next(
                    item["evidence_id"] for item in explanation_plan if item["id"] == target
                )
                question = catalog[evidence_id]
                print(f"EVIDENCE_ID: {evidence_id}\nQUESTION: {question}")
            else:
                question = input("QUESTION> ").strip()
        elif catalog_mode:
            remaining_evidence = set(catalog) - attempted_evidence
            if not remaining_evidence:
                break
            while True:
                evidence_id = input(
                    f"EVIDENCE_ID {index}/{budget}（{','.join(sorted(remaining_evidence))}；"
                    "输入 DECIDE 提前结束）> "
                ).strip()
                if evidence_id.upper() == "DECIDE":
                    break
                if evidence_id in remaining_evidence:
                    break
                print(
                    "无效或已尝试证据，请输入："
                    + ", ".join(sorted(remaining_evidence))
                )
            if evidence_id.upper() == "DECIDE":
                break
            question = catalog[evidence_id]
            print(f"QUESTION: {question}")
        else:
            question = input(
                f"QUESTION {index}/{budget}（输入 DECIDE 提前结束）> "
            ).strip()
            if question.upper() == "DECIDE":
                break
        if catalog_mode:
            assert evidence_id is not None
            fact_id, answer = answer_evidence_query(case, evidence_id, revealed)
            attempted_evidence.add(evidence_id)
        else:
            fact_id, answer = answer_question(case, question, revealed)
        if fact_id:
            revealed.add(fact_id)
        if target is not None:
            attempted_targets[target] = fact_id or "NONE"
        question_record = {
            "question": question,
            "fact_id": fact_id,
            "oracle_answer": answer,
            "manual_annotations": {
                "decision_changing": None,
                "discriminative": None,
                "unsupported_premise": None,
                "answerable": None,
                "decision_relevance_0_to_2": None,
                "branch_discrimination_0_to_2": None,
                "specificity_0_to_2": None,
                "data_gap_value": None,
            },
        }
        if catalog_mode:
            question_record["evidence_id"] = evidence_id
            question_record["oracle_mode"] = "evidence_catalog"
            question_record["manual_annotations"][
                "catalog_selection_relevance_0_to_2"
            ] = None
        if target is not None:
            question_record["explanation_target"] = target
            question_record["manual_annotations"].update(
                {
                    "target_alignment_0_to_2": None,
                    "semantic_target_reuse": None,
                }
            )
        questions.append(question_record)
        print(f"ORACLE: {answer}")

    post_decision = choose_option(case, "DECISION option_id> ")
    post_probabilities = choose_probabilities(
        case, 'PROBABILITIES JSON（例如 {"option_a":0.25,...}）> '
    )
    rationale = input("RATIONALE> ").strip()
    now = datetime.now(timezone.utc)
    session = {
        "benchmark_version": benchmark_version or BENCHMARK_VERSION,
        "case_id": case["case_id"],
        "public_case_id": case.get("public_case_id"),
        "pair_id": case.get("pair_id"),
        "variant_id": case.get("variant_id"),
        "condition": condition,
        "mode": "direct",
        "oracle_mode": "evidence_catalog" if catalog_mode else "keyword",
        "started_at_utc": now.isoformat(),
        "pre_decision": pre_decision,
        "pre_probabilities": pre_probabilities,
        "questions": questions,
        "post_decision": post_decision,
        "post_probabilities": post_probabilities,
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
    if stateful:
        session["explanation_state"] = {
            "plan": explanation_plan,
            "attempted_targets": attempted_targets,
            "manual_review": {
                "reviewer_id": None,
                "mechanism_distinctness_0_to_2": None,
                "action_coherence_0_to_2": None,
                "evidence_observability_0_to_2": None,
                "notes": None,
            },
        }
    if catalog_mode:
        session["evidence_state"] = {
            "attempted_evidence_ids": sorted(attempted_evidence),
            "catalog_size": len(catalog),
        }
    return save_session(case, session)


def run_api_session(
    case: dict[str, Any],
    condition: str,
    config: dict[str, Any],
    model_seed: int | None = None,
    prompt_file: str | None = None,
    benchmark_version: str | None = None,
) -> Path:
    stateful = uses_explanation_state(condition, prompt_file)
    catalog_mode = uses_evidence_catalog(condition, prompt_file)
    catalog_plan = uses_catalog_plan(condition, prompt_file)
    condition_prompt = (
        PROMPTS_DIR / (prompt_file or CONDITION_FILES[condition])
    ).read_text(encoding="utf-8")
    options = ", ".join(public_option_map(case))
    controller = (
        "\n\n你正由逐轮实验控制器调用。必须服从每条用户消息要求，"
        "每轮只输出所要求的一个协议字段，不要提前输出后续问题或结论。"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": condition_prompt + controller},
        {
            "role": "user",
            "content": (
                "## 公开案例\n"
                + json.dumps(
                    public_case(case, include_evidence_catalog=catalog_mode),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n\n现在只输出两行：\n"
                + f"PRE_DECISION: <option_id>（有效选项：{options}）\n"
                + "PRE_PROBABILITIES: <JSON 对象；包含全部选项，概率之和为 1>"
            ),
        },
    ]
    protocol_deviations: list[dict[str, str]] = []
    model_call_count = 0

    def call_model() -> str:
        nonlocal model_call_count
        model_call_count += 1
        return api_chat_completion(config, messages, model_seed)

    raw_pre = call_model()
    messages.append({"role": "assistant", "content": raw_pre})
    try:
        pre_value = parse_protocol_field(raw_pre, "PRE_DECISION")
        pre_probabilities = parse_probability_field(
            case, raw_pre, "PRE_PROBABILITIES"
        )
        if pre_value is None or pre_probabilities is None:
            raise ValueError("缺少 PRE_DECISION 或 PRE_PROBABILITIES")
        pre_decision = validate_option(case, pre_value, "PRE_DECISION")
    except ValueError as error:
        protocol_deviations.append(
            {
                "stage": "pre_decision",
                "type": "invalid_pre_protocol_repaired",
                "error": str(error),
                "raw_output": raw_pre,
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"格式无效：{error}。不要改变对案例的判断；仅修正格式。"
                    "重新只输出 PRE_DECISION 和 PRE_PROBABILITIES，概率必须包含"
                    " option_a、option_b、option_c、option_d 且总和为 1。"
                ),
            }
        )
        raw_pre = call_model()
        messages.append({"role": "assistant", "content": raw_pre})
        pre_value = parse_protocol_field(raw_pre, "PRE_DECISION")
        pre_probabilities = parse_probability_field(
            case, raw_pre, "PRE_PROBABILITIES"
        )
        if pre_value is None or pre_probabilities is None:
            raise ValueError(
                "模型在一次格式修复后仍未返回 PRE_DECISION/PRE_PROBABILITIES："
                f"{raw_pre[:300]}"
            )
        pre_decision = validate_option(case, pre_value, "PRE_DECISION")
        print("PROTOCOL_DEVIATION: 初始概率格式无效，已在一次重试后修复。")
    print(f"PRE_DECISION: {pre_decision}")
    print(
        "PRE_PROBABILITIES: "
        + json.dumps(pre_probabilities, ensure_ascii=False, separators=(",", ":"))
    )

    explanation_plan: list[dict[str, str]] = []
    attempted_targets: dict[str, str] = {}
    catalog = evidence_catalog_map(case) if catalog_mode else {}
    attempted_evidence: set[str] = set()
    if stateful:
        messages.append(
            {
                "role": "user",
                "content": (
                    "现在建立可检查的解释状态。只输出一行 EXPLANATIONS: <JSON 数组>。"
                    + (
                        "数组必须恰好包含 H1、H2、H3；每项包含 id、explanation、"
                        "evidence_id、action，且三个 evidence_id 与三个 action 都必须不同。"
                        if catalog_plan
                        else
                        "数组必须恰好包含 H1、H2、H3；每项包含 id、explanation、"
                        "evidence_target、action，且三个 action 必须不同。"
                    )
                ),
            }
        )
        raw_plan = call_model()
        messages.append({"role": "assistant", "content": raw_plan})
        try:
            explanation_plan = (
                parse_catalog_explanation_plan(case, raw_plan)
                if catalog_plan
                else parse_explanation_plan(case, raw_plan)
            )
        except ValueError as error:
            protocol_deviations.append(
                {
                    "stage": "explanation_plan",
                    "type": "invalid_explanation_plan_repaired",
                    "error": str(error),
                    "raw_output": raw_plan,
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"格式无效：{error}。不要改变解释内容，只修正格式。"
                        "重新只输出一行 EXPLANATIONS JSON 数组；必须恰好包含 H1、"
                        "H2、H3，并指向三个不同的有效 option_id"
                        + (
                            "和三个不同的有效 evidence_id。"
                            if catalog_plan
                            else "。"
                        )
                    ),
                }
            )
            raw_plan = call_model()
            messages.append({"role": "assistant", "content": raw_plan})
            explanation_plan = (
                parse_catalog_explanation_plan(case, raw_plan)
                if catalog_plan
                else parse_explanation_plan(case, raw_plan)
            )
            print("PROTOCOL_DEVIATION: 解释计划格式无效，已在一次重试后修复。")
        print(
            "EXPLANATIONS: "
            + json.dumps(explanation_plan, ensure_ascii=False, separators=(",", ":"))
        )

    revealed: set[str] = set()
    questions: list[dict[str, Any]] = []
    budget = case.get("question_budget", 5)
    for index in range(1, budget + 1):
        if stateful and len(attempted_targets) >= len(explanation_plan):
            break
        if catalog_mode and not stateful and len(attempted_evidence) >= len(catalog):
            break
        remaining_targets = {
            item["id"] for item in explanation_plan
        } - set(attempted_targets)
        remaining_evidence = set(catalog) - attempted_evidence
        if stateful and catalog_mode:
            request = (
                f"现在是第 {index}/{budget} 次证据选择。尚未尝试的解释目标："
                f"{', '.join(sorted(remaining_targets))}。只输出 TARGET: <一个尚未尝试的 id>；"
                "控制器将执行该解释预先绑定的证据问题。如果已经足够决策，只输出 DECIDE。"
            )
        elif stateful:
            request = (
                f"现在是第 {index}/{budget} 次提问机会。尚未尝试的解释目标："
                f"{', '.join(sorted(remaining_targets))}。只输出两行："
                "TARGET: <一个尚未尝试的 id> 和 QUESTION: <一个问题>；"
                "如果已经足够决策，只输出 DECIDE。"
            )
        elif catalog_mode:
            request = (
                f"现在是第 {index}/{budget} 次证据选择。尚未尝试的证据："
                f"{', '.join(sorted(remaining_evidence))}。"
                "只输出 EVIDENCE_ID: <一个尚未尝试的 id>；"
                "如果已经足够决策，只输出 DECIDE。"
            )
        else:
            request = (
                f"现在是第 {index}/{budget} 次提问机会。"
                "只输出 QUESTION: <一个问题>；如果已经足够决策，只输出 DECIDE。"
            )
        messages.append(
            {
                "role": "user",
                "content": request,
            }
        )
        raw_question = call_model()
        messages.append({"role": "assistant", "content": raw_question})
        if re.search(r"(?im)^\s*DECIDE\s*$", raw_question):
            break
        early_decision = parse_protocol_field(raw_question, "DECISION")
        missing_required_selection = (
            parse_protocol_field(raw_question, "TARGET") is None
            if stateful
            else (
                parse_protocol_field(raw_question, "EVIDENCE_ID") is None
                if catalog_mode
                else parse_protocol_field(raw_question, "QUESTION") is None
            )
        )
        if early_decision is not None and missing_required_selection:
            validate_option(case, early_decision, "DECISION")
            protocol_deviations.append(
                {
                    "stage": f"question_{index}",
                    "type": "early_decision_field",
                    "raw_output": raw_question,
                }
            )
            print(
                "PROTOCOL_DEVIATION: 模型使用 DECISION 提前结束提问；"
                "控制器将继续请求最终决定与理由。"
            )
            break
        target: str | None = None
        evidence_id: str | None = None
        question = parse_protocol_field(raw_question, "QUESTION")
        if stateful:
            valid_targets = {item["id"] for item in explanation_plan}
            try:
                if catalog_mode:
                    target = parse_protocol_field(raw_question, "TARGET")
                    if target not in valid_targets:
                        raise ValueError(
                            f"TARGET 必须是：{', '.join(sorted(valid_targets))}"
                        )
                    if target in attempted_targets:
                        raise ValueError(f"TARGET {target} 已尝试，不得复用")
                    evidence_id = next(
                        item["evidence_id"]
                        for item in explanation_plan
                        if item["id"] == target
                    )
                    if evidence_id in attempted_evidence:
                        raise ValueError(f"EVIDENCE_ID {evidence_id} 已尝试，不得复用")
                    question = catalog[evidence_id]
                else:
                    target, question = parse_targeted_question(
                        raw_question, valid_targets, set(attempted_targets)
                    )
            except ValueError as error:
                protocol_deviations.append(
                    {
                        "stage": f"question_{index}",
                        "type": "invalid_explanation_target_repaired",
                        "error": str(error),
                        "raw_output": raw_question,
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"格式或状态无效：{error}。只修正本轮输出；从尚未尝试的"
                            f" {', '.join(sorted(remaining_targets))} 中选择一个。"
                            + (
                                "只输出 TARGET 一行。"
                                if catalog_mode
                                else "只输出 TARGET 和 QUESTION 两行。"
                            )
                        ),
                    }
                )
                raw_question = call_model()
                messages.append({"role": "assistant", "content": raw_question})
                if catalog_mode:
                    target = parse_protocol_field(raw_question, "TARGET")
                    if target not in valid_targets or target in attempted_targets:
                        raise ValueError("一次修复后 TARGET 仍无效或已尝试")
                    evidence_id = next(
                        item["evidence_id"]
                        for item in explanation_plan
                        if item["id"] == target
                    )
                    if evidence_id in attempted_evidence:
                        raise ValueError("一次修复后 EVIDENCE_ID 仍已尝试")
                    question = catalog[evidence_id]
                else:
                    target, question = parse_targeted_question(
                        raw_question, valid_targets, set(attempted_targets)
                    )
                print(
                    "PROTOCOL_DEVIATION: 解释目标无效或已复用，"
                    "已在一次重试后修复。"
                )
        elif catalog_mode:
            try:
                evidence_id = parse_evidence_selection(
                    raw_question, set(catalog), attempted_evidence
                )
            except ValueError as error:
                protocol_deviations.append(
                    {
                        "stage": f"question_{index}",
                        "type": "invalid_evidence_selection_repaired",
                        "error": str(error),
                        "raw_output": raw_question,
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"格式或状态无效：{error}。只修正本轮输出；从尚未尝试的 "
                            f"{', '.join(sorted(remaining_evidence))} 中选择一个。"
                            "只输出 EVIDENCE_ID 一行。"
                        ),
                    }
                )
                raw_question = call_model()
                messages.append({"role": "assistant", "content": raw_question})
                evidence_id = parse_evidence_selection(
                    raw_question, set(catalog), attempted_evidence
                )
                print("PROTOCOL_DEVIATION: 证据选择无效或已复用，已在一次重试后修复。")
            question = catalog[evidence_id]
        elif question is None:
            raise ValueError(f"模型未按协议返回 QUESTION 或 DECIDE：{raw_question[:300]}")
        assert question is not None
        if catalog_mode:
            assert evidence_id is not None
            fact_id, answer = answer_evidence_query(case, evidence_id, revealed)
            attempted_evidence.add(evidence_id)
            keyword_fact_id = None
            semantic_raw = None
            oracle_mode = "evidence_catalog"
        else:
            keyword_fact_id, keyword_answer = answer_question(case, question, revealed)
            oracle_mode = config.get("oracle_mode", "semantic_api")
            semantic_raw = None
            if oracle_mode == "semantic_api":
                fact_id, answer, semantic_raw = semantic_answer_question(
                    case, question, revealed, config
                )
            elif oracle_mode == "keyword":
                fact_id, answer = keyword_fact_id, keyword_answer
            else:
                raise ValueError(f"不支持的 oracle_mode：{oracle_mode!r}")
        if fact_id:
            revealed.add(fact_id)
        if target is not None:
            attempted_targets[target] = fact_id or "NONE"
        question_record = {
            "question": question,
            "fact_id": fact_id,
            "oracle_answer": answer,
            "oracle_mode": oracle_mode,
            "keyword_fact_id": keyword_fact_id,
            "oracle_match_disagreement": (
                False if catalog_mode else keyword_fact_id != fact_id
            ),
            "semantic_oracle_raw": semantic_raw,
            "manual_annotations": {
                "decision_changing": None,
                "discriminative": None,
                "unsupported_premise": None,
                "answerable": None,
                "decision_relevance_0_to_2": None,
                "branch_discrimination_0_to_2": None,
                "specificity_0_to_2": None,
                "data_gap_value": None,
            },
        }
        if catalog_mode:
            question_record["evidence_id"] = evidence_id
            question_record["manual_annotations"][
                "catalog_selection_relevance_0_to_2"
            ] = None
        if target is not None:
            question_record["explanation_target"] = target
            question_record["manual_annotations"].update(
                {
                    "target_alignment_0_to_2": None,
                    "semantic_target_reuse": None,
                }
            )
        questions.append(question_record)
        print(f"QUESTION: {question}")
        print(f"ORACLE: {answer}")
        if stateful:
            rendered_state = ", ".join(
                f"{item['id']}={attempted_targets.get(item['id'], 'UNTRIED')}"
                for item in explanation_plan
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"ORACLE: {answer}\nSTATE: {rendered_state}"
                        + (
                            "\nUSED_EVIDENCE: "
                            + ", ".join(sorted(attempted_evidence))
                            if catalog_mode
                            else ""
                        )
                    ),
                }
            )
        else:
            messages.append({"role": "user", "content": f"ORACLE: {answer}"})

    messages.append(
        {
            "role": "user",
            "content": (
                "提问阶段结束。现在只输出三行：\n"
                f"DECISION: <option_id>（有效选项：{options}）\n"
                "PROBABILITIES: <JSON 对象；包含全部选项，概率之和为 1>\n"
                "RATIONALE: <不超过 100 字>"
            ),
        }
    )
    raw_final = call_model()
    messages.append({"role": "assistant", "content": raw_final})
    try:
        decision_value = parse_protocol_field(raw_final, "DECISION")
        post_probabilities = parse_probability_field(case, raw_final, "PROBABILITIES")
        rationale = parse_protocol_field(raw_final, "RATIONALE")
        if decision_value is None or post_probabilities is None or rationale is None:
            raise ValueError("缺少 DECISION、PROBABILITIES 或 RATIONALE")
        post_decision = validate_option(case, decision_value, "DECISION")
    except ValueError as error:
        protocol_deviations.append(
            {
                "stage": "final_decision",
                "type": "invalid_final_protocol_repaired",
                "error": str(error),
                "raw_output": raw_final,
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"格式无效：{error}。不要改变判断；仅修正格式。重新只输出 "
                    "DECISION、PROBABILITIES、RATIONALE；四项概率之和必须为 1。"
                ),
            }
        )
        raw_final = call_model()
        decision_value = parse_protocol_field(raw_final, "DECISION")
        post_probabilities = parse_probability_field(case, raw_final, "PROBABILITIES")
        rationale = parse_protocol_field(raw_final, "RATIONALE")
        if decision_value is None or post_probabilities is None or rationale is None:
            raise ValueError(
                "模型在一次格式修复后仍未返回完整最终协议："
                f"{raw_final[:300]}"
            )
        post_decision = validate_option(case, decision_value, "DECISION")
        print("PROTOCOL_DEVIATION: 最终概率格式无效，已在一次重试后修复。")
    print(f"DECISION: {post_decision}")
    print(
        "PROBABILITIES: "
        + json.dumps(post_probabilities, ensure_ascii=False, separators=(",", ":"))
    )
    print(f"RATIONALE: {rationale}")

    now = datetime.now(timezone.utc)
    session = {
        "benchmark_version": benchmark_version or BENCHMARK_VERSION,
        "case_id": case["case_id"],
        "public_case_id": case.get("public_case_id"),
        "pair_id": case.get("pair_id"),
        "variant_id": case.get("variant_id"),
        "condition": condition,
        "mode": "api",
        "model_name": config["model_name"],
        "oracle_mode": (
            "evidence_catalog"
            if catalog_mode
            else config.get("oracle_mode", "semantic_api")
        ),
        "oracle_model_name": (
            (config.get("oracle_model_name") or config["model_name"])
            if not catalog_mode
            and config.get("oracle_mode", "semantic_api") == "semantic_api"
            else None
        ),
        "model_seed": model_seed,
        "model_call_count": model_call_count,
        "started_at_utc": now.isoformat(),
        "pre_decision": pre_decision,
        "pre_probabilities": pre_probabilities,
        "questions": questions,
        "post_decision": post_decision,
        "post_probabilities": post_probabilities,
        "rationale": rationale,
        "protocol_deviations": protocol_deviations,
        "manual_review": {
            "reviewer_id": None,
            "false_balance": None,
            "sensitive_information_risk": None,
            "user_burden_1_to_5": None,
            "oracle_errors": [],
            "notes": None,
        },
    }
    if stateful:
        session["explanation_state"] = {
            "plan": explanation_plan,
            "attempted_targets": attempted_targets,
            "manual_review": {
                "reviewer_id": None,
                "mechanism_distinctness_0_to_2": None,
                "action_coherence_0_to_2": None,
                "evidence_observability_0_to_2": None,
                "notes": None,
            },
        }
    if catalog_mode:
        session["evidence_state"] = {
            "attempted_evidence_ids": sorted(attempted_evidence),
            "catalog_size": len(catalog),
        }
    return save_session(case, session)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Question Discovery Benchmark v{BENCHMARK_VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("list")
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("case_id")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("case_id")
    run_parser.add_argument("--condition", choices=CONDITION_FILES, required=True)
    run_parser.add_argument("--mode", choices=RUN_MODES)
    run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run_parser.add_argument("--model-seed", type=int)
    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("session_file", type=Path)
    config_parser = subparsers.add_parser("check-config")
    config_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    schedule_parser = subparsers.add_parser("schedule")
    schedule_parser.add_argument("--seed", type=int, default=20260819)
    schedule_parser.add_argument("--output", type=Path)
    calibration_parser = subparsers.add_parser("calibration-schedule")
    calibration_parser.add_argument("--seed", type=int, default=20260819)
    calibration_parser.add_argument("--output", type=Path)
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
            "benchmark_version": BENCHMARK_VERSION,
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
    if args.command == "calibration-schedule":
        schedule = {
            "benchmark_version": BENCHMARK_VERSION,
            "randomization_seed": args.seed,
            "total_runs": len(cases),
            "runs": build_calibration_schedule(cases, args.seed),
        }
        rendered = json.dumps(schedule, ensure_ascii=False, indent=2)
        if args.output:
            output = args.output
            if not output.is_absolute():
                output = ROOT / output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"Calibration schedule saved: {output} ({schedule['total_runs']} runs)")
        else:
            print(rendered)
        return 0
    if args.command == "check-config":
        try:
            config = load_model_config(args.config)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
            print(f"配置无效：{error}")
            return 1
        print("配置有效：")
        print(f"- endpoint: {completion_endpoint(config['url'])}")
        print(f"- model_name: {config['model_name']}")
        print(f"- api_key: {'已填写' if config.get('api_key') else '未填写'}")
        print(f"- oracle_mode: {config.get('oracle_mode', 'semantic_api')}")
        if config.get("oracle_mode", "semantic_api") == "semantic_api":
            print(
                "- oracle_model_name: "
                f"{config.get('oracle_model_name') or config['model_name']}"
            )
        return 0
    if args.command in {"show", "run"} and args.case_id not in cases:
        parser.error(f"unknown case_id: {args.case_id}")
    if args.command == "show":
        print(json.dumps(public_case(cases[args.case_id]), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        mode = args.mode or choose_run_mode()
        if mode == "api":
            try:
                config = load_model_config(args.config)
                run_api_session(
                    cases[args.case_id], args.condition, config, args.model_seed
                )
            except (
                FileNotFoundError,
                ValueError,
                RuntimeError,
                json.JSONDecodeError,
            ) as error:
                print(f"API 运行失败：{error}")
                return 1
        else:
            run_direct_session(cases[args.case_id], args.condition)
        return 0
    if args.command == "score":
        session = json.loads(args.session_file.read_text(encoding="utf-8"))
        case = cases[session["case_id"]]
        print(json.dumps(score_session(case, session), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
