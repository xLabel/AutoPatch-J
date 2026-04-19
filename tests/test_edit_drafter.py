from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from autopatch_j.edit_drafter import (
    DraftedEdit,
    LLMEditDrafter,
    build_default_edit_drafter,
    build_edit_draft_payload,
    parse_edit_draft_response,
    render_edit_draft_prompt,
)
from autopatch_j.llm import LLMResponse


class FakeDraftClient:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.model = "deepseek-chat"
        self.label = "openai-compatible:deepseek-chat"
        self.calls: list[dict[str, object]] = []

    def complete(self, **payload: object) -> LLMResponse:
        self.calls.append(payload)
        return self.response


class EditDrafterTests(unittest.TestCase):
    def test_build_payload_uses_chat_messages_and_json_object_format(self) -> None:
        payload = build_edit_draft_payload(
            model="deepseek-chat",
            file_path="Demo.java",
            instruction="guard string compare",
            file_content="class Demo {}",
        )

        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")

    def test_parse_response_returns_drafted_edit(self) -> None:
        drafted = parse_edit_draft_response(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"file_path":"Demo.java","old_string":"call();",'
                                    '"new_string":"safeCall();","rationale":"Minimal guard."}'
                                ),
                            }
                        ],
                    }
                ]
            },
            expected_file_path="Demo.java",
        )

        self.assertEqual(drafted.file_path, "Demo.java")
        self.assertEqual(drafted.old_string, "call();")

    def test_drafter_raises_on_file_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            parse_edit_draft_response(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        '{"file_path":"Other.java","old_string":"call();",'
                                        '"new_string":"safeCall();","rationale":"Minimal guard."}'
                                    ),
                                }
                            ],
                        }
                    ]
                },
                expected_file_path="Demo.java",
            )

    def test_llm_edit_drafter_calls_client_without_streaming(self) -> None:
        client = FakeDraftClient(
            LLMResponse(
                content=(
                    '{"file_path":"Demo.java","old_string":"call();",'
                    '"new_string":"safeCall();","rationale":"Minimal guard."}'
                )
            )
        )
        drafter = LLMEditDrafter(client)
        drafted = drafter.draft_edit("Demo.java", "guard string compare", "call();")

        self.assertEqual(drafted.new_string, "safeCall();")
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.calls[0]["stream"])
        self.assertEqual(client.calls[0]["response_format"], {"type": "json_object"})

    def test_render_prompt_includes_previous_draft_feedback(self) -> None:
        prompt = render_edit_draft_prompt(
            file_path="Demo.java",
            instruction="guard string compare",
            file_content="call();",
            previous_edit=DraftedEdit(
                file_path="Demo.java",
                old_string="missing();",
                new_string="safeCall();",
                rationale="first try",
            ),
            feedback="preview_status: missing",
        )

        self.assertIn("Previous draft:", prompt)
        self.assertIn('"old_string": "missing();"', prompt)
        self.assertIn("Repair feedback:", prompt)
        self.assertIn("preview_status: missing", prompt)

    def test_build_default_edit_drafter_returns_none_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            drafter = build_default_edit_drafter()
        self.assertIsNone(drafter)


if __name__ == "__main__":
    unittest.main()
