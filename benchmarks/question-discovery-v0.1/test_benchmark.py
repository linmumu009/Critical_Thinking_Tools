import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import benchmark


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = benchmark.load_cases()

    def test_all_cases_validate(self):
        self.assertEqual([], benchmark.validate_all(self.cases))

    def test_public_case_does_not_expose_hidden_fields(self):
        hidden = {"oracle_facts", "key_unknowns", "utility", "hypotheses", "leakage_terms"}
        for case in self.cases.values():
            self.assertTrue(hidden.isdisjoint(benchmark.public_case(case)))

    def test_oracle_reveals_at_most_one_new_fact(self):
        for case in self.cases.values():
            first_fact = case["oracle_facts"][0]
            question = f"请说明{first_fact['triggers'][0]}方面的数据"
            fact_id, answer = benchmark.answer_question(case, question, set())
            self.assertIsNotNone(fact_id)
            self.assertTrue(answer)
            next_fact_id, _ = benchmark.answer_question(case, question, {fact_id})
            self.assertNotEqual(fact_id, next_fact_id)

    def test_unknown_question_returns_no_fact(self):
        case = next(iter(self.cases.values()))
        fact_id, answer = benchmark.answer_question(
            case, "月球背面的紫色独角兽有多少只", set()
        )
        self.assertIsNone(fact_id)
        self.assertIn("无法回答", answer)

    def test_research_timeline_follow_up_reaches_weekly_trajectory(self):
        case = self.cases["research-01"]
        question = (
            "复制研究在第 12 周主终点之前，是否收集过与试点相同的短期结局"
            "指标（如第 2 周），如果有，结果如何？"
        )
        fact_id, answer = benchmark.answer_question(case, question, {"f1"})
        self.assertEqual("f2", fact_id)
        self.assertIn("第 6 周", answer)

    def test_score_session(self):
        case = next(iter(self.cases.values()))
        best = case["utility"]["best_option"]
        worst = min(
            case["utility"]["option_scores"],
            key=case["utility"]["option_scores"].get,
        )
        critical = next(
            fact for fact in case["oracle_facts"] if fact["criticality"] == "critical"
        )
        session = {
            "pre_decision": worst,
            "post_decision": best,
            "questions": [{"fact_id": critical["id"]}],
        }
        metrics = benchmark.score_session(case, session)
        self.assertGreater(metrics["decision_improvement"], 0)
        self.assertEqual(1.0, metrics["normalized_post_utility"])
        self.assertGreater(metrics["critical_fact_hit_rate"], 0)

    def test_schedule_is_reproducible_and_balanced(self):
        first = benchmark.build_schedule(self.cases, 123)
        second = benchmark.build_schedule(self.cases, 123)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 108)
        combinations = {
            (run["case_id"], run["condition"], run["model_seed"])
            for run in first
        }
        self.assertEqual(len(combinations), 108)
        for case_id in self.cases:
            for condition in benchmark.CONDITION_FILES:
                count = sum(
                    run["case_id"] == case_id
                    and run["condition"] == condition
                    for run in first
                )
                self.assertEqual(count, 3)

    def test_completion_endpoint_accepts_base_or_full_url(self):
        self.assertEqual(
            "https://example.test/v1/chat/completions",
            benchmark.completion_endpoint("https://example.test/v1"),
        )
        self.assertEqual(
            "https://example.test/v1/chat/completions",
            benchmark.completion_endpoint(
                "https://example.test/v1/chat/completions"
            ),
        )

    def test_protocol_field_parser(self):
        text = "PRE_DECISION: observe\n其他内容"
        self.assertEqual(
            "observe", benchmark.parse_protocol_field(text, "PRE_DECISION")
        )
        self.assertIsNone(benchmark.parse_protocol_field(text, "DECISION"))

    def test_mode_is_selected_explicitly(self):
        with patch("builtins.input", return_value="1"):
            self.assertEqual("api", benchmark.choose_run_mode())
        with patch("builtins.input", return_value="2"):
            self.assertEqual("direct", benchmark.choose_run_mode())

    def test_api_request_uses_config_without_exposing_key_in_body(self):
        config = {
            "url": "https://example.test/v1",
            "api_key": "top-secret",
            "model_name": "test-model",
        }
        response = FakeResponse(
            {"choices": [{"message": {"content": "PRE_DECISION: observe"}}]}
        )
        with patch.object(
            benchmark.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            text = benchmark.api_chat_completion(
                config, [{"role": "user", "content": "case"}], model_seed=3
            )

        self.assertEqual("PRE_DECISION: observe", text)
        request = urlopen.call_args.args[0]
        self.assertEqual(
            "https://example.test/v1/chat/completions", request.full_url
        )
        self.assertEqual("Bearer top-secret", request.get_header("Authorization"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual("test-model", body["model"])
        self.assertEqual(3, body["seed"])
        self.assertNotIn("top-secret", request.data.decode("utf-8"))

    def test_api_session_runs_sequentially_without_hidden_case_leak(self):
        case = self.cases["product-01"]
        config = {
            "url": "https://example.test/v1",
            "api_key": "secret-not-stored-in-session",
            "model_name": "test-model",
        }
        outputs = [
            "PRE_DECISION: observe",
            "QUESTION: 下降按设备和浏览器如何分布？",
            "DECIDE",
            "DECISION: rollback_release\nRATIONALE: 根据客户端分布先回滚。",
        ]
        with patch.object(
            benchmark, "api_chat_completion", side_effect=outputs
        ) as completion, patch.object(
            benchmark, "save_session", return_value=Path("session.json")
        ) as save:
            path = benchmark.run_api_session(case, "A", config, model_seed=7)

        self.assertEqual(Path("session.json"), path)
        self.assertEqual(4, completion.call_count)
        session = save.call_args.args[1]
        self.assertEqual("api", session["mode"])
        self.assertEqual("test-model", session["model_name"])
        self.assertNotIn("api_key", session)
        self.assertEqual("f1", session["questions"][0]["fact_id"])
        first_messages = completion.call_args_list[0].args[1]
        self.assertNotIn("oracle_facts", first_messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
