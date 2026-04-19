from __future__ import annotations

import json
import os
from dataclasses import dataclass

from autopatch_j.openai_responses import OpenAIResponsesClient


@dataclass(slots=True)
class DraftedEdit:
    file_path: str
    old_string: str
    new_string: str
    rationale: str


class OpenAIEditDrafter:
    def __init__(self, client: OpenAIResponsesClient) -> None:
        self.client = client

    @property
    def label(self) -> str:
        return f"openai:{self.client.model}"

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        payload = build_edit_draft_payload(
            model=self.client.model,
            file_path=file_path,
            instruction=instruction,
            file_content=file_content,
        )
        response = self.client.create_response(payload)
        return parse_edit_draft_response(response, expected_file_path=file_path)


def build_default_edit_drafter() -> OpenAIEditDrafter | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("AUTOPATCH_OPENAI_MODEL", "gpt-5.4-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAIEditDrafter(
        OpenAIResponsesClient(api_key=api_key, model=model, base_url=base_url)
    )


def build_edit_draft_payload(
    model: str,
    file_path: str,
    instruction: str,
    file_content: str,
) -> dict[str, object]:
    return {
        "model": model,
        "instructions": EDIT_DRAFT_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": render_edit_draft_prompt(file_path, instruction, file_content),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "search_replace_edit",
                "strict": True,
                "schema": EDIT_DRAFT_SCHEMA,
            }
        },
    }


def parse_edit_draft_response(response: dict[str, object], expected_file_path: str) -> DraftedEdit:
    raw = extract_response_text(response)
    if not raw:
        raise ValueError("OpenAI returned no draft edit content.")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("OpenAI returned a non-object draft edit payload.")

    file_path = str(payload.get("file_path", ""))
    if file_path != expected_file_path:
        raise ValueError(
            f"Draft file_path mismatch: expected {expected_file_path}, got {file_path or '(empty)'}"
        )

    return DraftedEdit(
        file_path=file_path,
        old_string=str(payload.get("old_string", "")),
        new_string=str(payload.get("new_string", "")),
        rationale=str(payload.get("rationale", "")),
    )


def extract_response_text(response: dict[str, object]) -> str:
    output = response.get("output", [])
    if not isinstance(output, list):
        return ""

    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"}:
                text = block.get("text", "")
                if text:
                    texts.append(str(text))
    return "\n".join(texts).strip()


def render_edit_draft_prompt(file_path: str, instruction: str, file_content: str) -> str:
    return (
        f"Target file:\n{file_path}\n\n"
        f"Instruction:\n{instruction}\n\n"
        "Current file content:\n"
        "```text\n"
        f"{file_content}\n"
        "```\n"
    )


EDIT_DRAFT_INSTRUCTIONS = (
    "You are generating a minimal search-replace edit for AutoPatch-J. "
    "Return exactly one edit for the given target file. "
    "The old_string must match a unique span from the current file content. "
    "Keep the change as small as possible and avoid introducing new dependencies."
)


EDIT_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["file_path", "old_string", "new_string", "rationale"],
    "additionalProperties": False,
}
