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


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.get("public_case_id", case["case_id"]),
        "domain": case["domain"],
        "title": case["title"],
        "brief": case["brief"],
        "decision_deadline": case["decision"]["deadline"],
        "options": public_options(case),
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

    if case.get("question_budget", 5) != 5:
        errors.append("benchmark cases must use a five-question budget")
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
    prompt = (PROMPTS_DIR / (prompt_file or CONDITION_FILES[condition])).read_text(
        encoding="utf-8"
    )
    payload = public_case(case)
    print(prompt)
    print("\n## 公开案例\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n请在全新模型对话中使用以上内容，并把模型输出复制回来。\n")

    pre_decision = choose_option(case, "PRE_DECISION option_id> ")
    pre_probabilities = choose_probabilities(
        case, 'PRE_PROBABILITIES JSON（例如 {"option_a":0.25,...}）> '
    )
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
                    "decision_relevance_0_to_2": None,
                    "branch_discrimination_0_to_2": None,
                    "specificity_0_to_2": None,
                    "data_gap_value": None,
                },
            }
        )
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
    return save_session(case, session)


def run_api_session(
    case: dict[str, Any],
    condition: str,
    config: dict[str, Any],
    model_seed: int | None = None,
    prompt_file: str | None = None,
    benchmark_version: str | None = None,
) -> Path:
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
                + json.dumps(public_case(case), ensure_ascii=False, indent=2)
                + "\n\n现在只输出两行：\n"
                + f"PRE_DECISION: <option_id>（有效选项：{options}）\n"
                + "PRE_PROBABILITIES: <JSON 对象；包含全部选项，概率之和为 1>"
            ),
        },
    ]
    protocol_deviations: list[dict[str, str]] = []
    raw_pre = api_chat_completion(config, messages, model_seed)
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
        raw_pre = api_chat_completion(config, messages, model_seed)
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

    revealed: set[str] = set()
    questions: list[dict[str, Any]] = []
    budget = case.get("question_budget", 5)
    for index in range(1, budget + 1):
        messages.append(
            {
                "role": "user",
                "content": (
                    f"现在是第 {index}/{budget} 次提问机会。只输出 QUESTION: <一个问题>；"
                    "如果已经足够决策，只输出 DECIDE。"
                ),
            }
        )
        raw_question = api_chat_completion(config, messages, model_seed)
        messages.append({"role": "assistant", "content": raw_question})
        if re.search(r"(?im)^\s*DECIDE\s*$", raw_question):
            break
        question = parse_protocol_field(raw_question, "QUESTION")
        early_decision = parse_protocol_field(raw_question, "DECISION")
        if question is None and early_decision is not None:
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
        if question is None:
            raise ValueError(f"模型未按协议返回 QUESTION 或 DECIDE：{raw_question[:300]}")
        keyword_fact_id, keyword_answer = answer_question(case, question, revealed)
        oracle_mode = config.get("oracle_mode", "semantic_api")
        semantic_raw: str | None = None
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
        questions.append(
            {
                "question": question,
                "fact_id": fact_id,
                "oracle_answer": answer,
                "oracle_mode": oracle_mode,
                "keyword_fact_id": keyword_fact_id,
                "oracle_match_disagreement": keyword_fact_id != fact_id,
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
        )
        print(f"QUESTION: {question}")
        print(f"ORACLE: {answer}")
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
    raw_final = api_chat_completion(config, messages, model_seed)
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
        raw_final = api_chat_completion(config, messages, model_seed)
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
        "oracle_mode": config.get("oracle_mode", "semantic_api"),
        "oracle_model_name": (
            (config.get("oracle_model_name") or config["model_name"])
            if config.get("oracle_mode", "semantic_api") == "semantic_api"
            else None
        ),
        "model_seed": model_seed,
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
