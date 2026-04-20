from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from autopatch_j.llm import LLM, LLMResponse, build_default_llm


@dataclass(slots=True)
class DraftedEdit:
    file_path: str
    old_string: str
    new_string: str
    rationale: str


class EditDrafter(Protocol):
    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        """Return one minimal search-replace draft for the target file."""

    @property
    def label(self) -> str:
        """A short label for status output."""


class RepairingEditDrafter(EditDrafter, Protocol):
    def redraft_edit(
        self,
        file_path: str,
        instruction: str,
        file_content: str,
        previous_edit: DraftedEdit,
        feedback: str,
    ) -> DraftedEdit:
        """Return a corrected draft after preview or syntax feedback."""


class LLMEditDrafter:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    @property
    def label(self) -> str:
        return self.llm.label

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        response = self.llm.chat(
            messages=build_edit_draft_messages(
                file_path=file_path,
                instruction=instruction,
                file_content=file_content,
            ),
            response_format={"type": "json_object"},
            stream=False,
        )
        return parse_edit_draft_response(response, expected_file_path=file_path)

    def redraft_edit(
        self,
        file_path: str,
        instruction: str,
        file_content: str,
        previous_edit: DraftedEdit,
        feedback: str,
    ) -> DraftedEdit:
        response = self.llm.chat(
            messages=build_edit_draft_messages(
                file_path=file_path,
                instruction=instruction,
                file_content=file_content,
                previous_edit=previous_edit,
                feedback=feedback,
            ),
            response_format={"type": "json_object"},
            stream=False,
        )
        return parse_edit_draft_response(response, expected_file_path=file_path)


def build_default_edit_drafter() -> LLMEditDrafter | None:
    llm = build_default_llm()
    if llm is None:
        return None
    return LLMEditDrafter(llm)


def build_edit_draft_messages(
    file_path: str,
    instruction: str,
    file_content: str,
    previous_edit: DraftedEdit | None = None,
    feedback: str | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": EDIT_DRAFT_INSTRUCTIONS},
        {
            "role": "user",
            "content": render_edit_draft_prompt(
                file_path=file_path,
                instruction=instruction,
                file_content=file_content,
                previous_edit=previous_edit,
                feedback=feedback,
            ),
        },
    ]


def parse_edit_draft_response(response: LLMResponse | dict[str, object], expected_file_path: str) -> DraftedEdit:
    raw = extract_draft_response_text(response)
    if not raw:
        raise ValueError("LLM returned no draft edit content.")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("LLM returned a non-object draft edit payload.")

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


def extract_draft_response_text(response: LLMResponse | dict[str, object]) -> str:
    if isinstance(response, LLMResponse):
        return response.content.strip()

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


def render_edit_draft_prompt(
    file_path: str,
    instruction: str,
    file_content: str,
    previous_edit: DraftedEdit | None = None,
    feedback: str | None = None,
) -> str:
    prompt = (
        f"Target file:\n{file_path}\n\n"
        f"Instruction:\n{instruction}\n\n"
        "Current file content:\n"
        "```text\n"
        f"{file_content}\n"
        "```\n"
    )
    if previous_edit is None and not feedback:
        return prompt

    previous = previous_edit or DraftedEdit(
        file_path=file_path,
        old_string="",
        new_string="",
        rationale="",
    )
    return (
        f"{prompt}\n"
        "Previous draft:\n"
        "```json\n"
        "{\n"
        f'  "file_path": {json.dumps(previous.file_path)},\n'
        f'  "old_string": {json.dumps(previous.old_string)},\n'
        f'  "new_string": {json.dumps(previous.new_string)},\n'
        f'  "rationale": {json.dumps(previous.rationale)}\n'
        "}\n"
        "```\n\n"
        "Repair feedback:\n"
        f"{feedback or '(none)'}\n"
    )


EDIT_DRAFT_INSTRUCTIONS = (
    "You generate one minimal search-replace edit for AutoPatch-J. "
    "Return only a JSON object with keys: file_path, old_string, new_string, rationale. "
    "The old_string must match a unique span from the current file content. "
    "Keep the change as small as possible and avoid introducing new dependencies. "
    "If previous draft feedback is provided, correct that failed draft instead of repeating it."
)
