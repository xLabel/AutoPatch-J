from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from autopatch_j.edit_drafter import (
    DraftedEdit,
    OpenAIEditDrafter,
    build_default_edit_drafter,
    build_edit_draft_payload,
    parse_edit_draft_response,
    render_edit_draft_prompt,
)


class FakeDraftClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.model = "gpt-5.4-mini"
        self.payloads: list[dict[str, object]] = []

    def create_response(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        return self.response


class EditDrafterTests(unittest.TestCase):
    def test_build_payload_uses_json_schema_format(self) -> None:
        payload = build_edit_draft_payload(
            model="gpt-5.4-mini",
            file_path="Demo.java",
            instruction="guard string compare",
            file_content="class Demo {}",
        )

        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(payload["text"]["format"]["name"], "search_replace_edit")

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

    def test_openai_edit_drafter_calls_client(self) -> None:
        client = FakeDraftClient(
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
            }
        )
        drafter = OpenAIEditDrafter(client)
        drafted = drafter.draft_edit("Demo.java", "guard string compare", "call();")

        self.assertEqual(drafted.new_string, "safeCall();")
        self.assertEqual(len(client.payloads), 1)

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
