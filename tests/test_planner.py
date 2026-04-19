from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from autopatch_j.planner import (
    AGENT_ACTION_TOOLS,
    DecisionContext,
    build_default_planner,
    build_decision_messages,
    LLMPlanner,
    parse_llm_decision_response,
    render_decision_prompt,
    UnavailablePlanner,
)
from autopatch_j.llm import LLMResponse, LLMToolCall


class FakeClient:
    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None) -> None:
        self.response = response or LLMResponse()
        self.error = error
        self.model = "deepseek-chat"
        self.label = "chat-completions:deepseek-chat"
        self.calls: list[dict[str, object]] = []

    def complete(self, **payload: object) -> LLMResponse:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.response


class PlannerTests(unittest.TestCase):
    def test_build_messages_contains_user_context(self) -> None:
        messages = build_decision_messages(
            DecisionContext(
                user_text="扫描整个仓库的问题",
                scoped_paths=["src/main/java/demo/App.java"],
                has_active_findings=False,
                mention_context="- src/main/java/demo/App.java (file)",
            ),
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("扫描整个仓库的问题", messages[1]["content"])
        self.assertEqual(AGENT_ACTION_TOOLS[0]["function"]["name"], "scan")

    def test_render_decision_prompt_includes_mention_context(self) -> None:
        prompt = render_decision_prompt(
            DecisionContext(
                user_text="scan this file",
                scoped_paths=["src/main/java/demo/App.java"],
                has_active_findings=False,
                mention_context="- src/main/java/demo/App.java (file)\n```text\nclass App {}\n```",
            )
        )

        self.assertIn("Mention context:", prompt)
        self.assertIn("class App {}", prompt)

    def test_parse_response_returns_scan_action(self) -> None:
        decision = parse_llm_decision_response(
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="scan",
                        arguments={"scope": ["src/main/java/demo/App.java"]},
                    )
                ]
            ),
            DecisionContext(
                user_text="scan this file",
                scoped_paths=["src/main/java/demo/App.java"],
                has_active_findings=False,
            ),
        )

        self.assertEqual(decision.action, "scan")
        self.assertEqual(decision.tool_name, "scan_java")
        self.assertEqual(decision.tool_args["scope"], ["src/main/java/demo/App.java"])

    def test_parse_response_returns_patch_action(self) -> None:
        decision = parse_llm_decision_response(
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="patch",
                        arguments={"finding_index": 2},
                    )
                ]
            ),
            DecisionContext(
                user_text="修复第2个问题",
                scoped_paths=[],
                has_active_findings=True,
            ),
        )

        self.assertEqual(decision.action, "patch")
        self.assertEqual(decision.tool_args["finding_index"], 2)

    def test_parse_response_returns_text_message(self) -> None:
        decision = parse_llm_decision_response(
            LLMResponse(content="No scan is needed."),
            DecisionContext(
                user_text="explain this class",
                scoped_paths=[],
                has_active_findings=False,
            ),
        )

        self.assertEqual(decision.action, "answer")
        self.assertEqual(decision.message, "No scan is needed.")

    def test_engine_calls_client_with_streaming_tools(self) -> None:
        engine = LLMPlanner(
            client=FakeClient(
                response=LLMResponse(
                    tool_calls=[LLMToolCall(name="scan", arguments={"scope": ["."]})]
                )
            )
        )

        decision = engine.decide(
            DecisionContext(
                user_text="scan this repository",
                scoped_paths=[],
                has_active_findings=False,
            )
        )

        self.assertEqual(decision.action, "scan")
        self.assertEqual(engine.client.calls[0]["tools"], AGENT_ACTION_TOOLS)
        self.assertTrue(engine.client.calls[0]["stream"])

    def test_engine_does_not_fall_back_to_rules_when_client_errors(self) -> None:
        engine = LLMPlanner(client=FakeClient(error=RuntimeError("boom")))
        decision = engine.decide(
            DecisionContext(
                user_text="scan this repository",
                scoped_paths=[],
                has_active_findings=False,
            )
        )

        self.assertEqual(decision.action, "answer")
        self.assertIn("LLM planner failed", decision.message)

    def test_build_default_engine_is_unavailable_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            engine = build_default_planner()
        self.assertEqual(engine.label, "llm:unavailable")
        self.assertIsInstance(engine, UnavailablePlanner)


if __name__ == "__main__":
    unittest.main()
