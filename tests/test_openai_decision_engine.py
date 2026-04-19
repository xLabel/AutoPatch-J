from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from autopatch_j.decision_engine import (
    DecisionContext,
    OpenAIDecisionEngine,
    RuleBasedDecisionEngine,
    build_default_decision_engine,
    build_openai_decision_payload,
    render_decision_prompt,
    parse_openai_decision_response,
)


class FakeClient:
    def __init__(self, response: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.model = "gpt-5.4-mini"
        self.payloads: list[dict[str, object]] = []

    def create_response(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.response


class OpenAIDecisionEngineTests(unittest.TestCase):
    def test_build_payload_contains_scan_tool(self) -> None:
        payload = build_openai_decision_payload(
            "gpt-5.4-mini",
            DecisionContext(
                user_text="扫描整个仓库的问题",
                scoped_paths=["src/main/java/demo/App.java"],
                has_active_findings=False,
                mention_context="- src/main/java/demo/App.java (file)",
            ),
        )

        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["tools"][0]["name"], "scan_java")

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

    def test_parse_response_returns_tool_call(self) -> None:
        decision = parse_openai_decision_response(
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "scan_java",
                        "arguments": '{"scope":["src/main/java/demo/App.java"]}',
                    }
                ]
            },
            DecisionContext(
                user_text="scan this file",
                scoped_paths=["src/main/java/demo/App.java"],
                has_active_findings=False,
            ),
        )

        self.assertEqual(decision.action, "tool_call")
        self.assertEqual(decision.tool_name, "scan_java")
        self.assertEqual(decision.tool_args["scope"], ["src/main/java/demo/App.java"])

    def test_parse_response_returns_text_message(self) -> None:
        decision = parse_openai_decision_response(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "No scan is needed."}],
                    }
                ]
            },
            DecisionContext(
                user_text="explain this class",
                scoped_paths=[],
                has_active_findings=False,
            ),
        )

        self.assertEqual(decision.action, "respond")
        self.assertEqual(decision.message, "No scan is needed.")

    def test_engine_falls_back_when_client_errors(self) -> None:
        engine = OpenAIDecisionEngine(
            client=FakeClient(error=RuntimeError("boom")),
            fallback=RuleBasedDecisionEngine(),
        )

        decision = engine.decide(
            DecisionContext(
                user_text="scan this repository",
                scoped_paths=[],
                has_active_findings=False,
            )
        )

        self.assertEqual(decision.action, "tool_call")
        self.assertIn("fell back", decision.message)

    def test_build_default_engine_uses_rule_based_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            engine = build_default_decision_engine()
        self.assertEqual(engine.label, "rule-based")


if __name__ == "__main__":
    unittest.main()
