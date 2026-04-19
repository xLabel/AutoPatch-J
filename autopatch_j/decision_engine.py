from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Literal, Protocol

from autopatch_j.intent import has_scan_intent
from autopatch_j.openai_responses import OpenAIResponsesClient


@dataclass(slots=True)
class DecisionContext:
    user_text: str
    scoped_paths: list[str]
    has_active_findings: bool


@dataclass(slots=True)
class AgentDecision:
    action: Literal["respond", "tool_call"]
    message: str
    tool_name: str | None = None
    tool_args: dict[str, object] = field(default_factory=dict)


class DecisionEngine(Protocol):
    def decide(self, context: DecisionContext) -> AgentDecision:
        """Return the next agent action for the current turn."""

    @property
    def label(self) -> str:
        """A short label for status output."""


class RuleBasedDecisionEngine:
    @property
    def label(self) -> str:
        return "rule-based"

    def decide(self, context: DecisionContext) -> AgentDecision:
        if has_scan_intent(context.user_text):
            scope = context.scoped_paths or ["."]
            return AgentDecision(
                action="tool_call",
                message="Detected scan intent from the current prompt.",
                tool_name="scan_java",
                tool_args={"scope": scope},
            )

        return AgentDecision(
            action="respond",
            message=(
                "No tool call is needed for this prompt.\n"
                "Current router is rule-based and will be replaced by an LLM decision engine later."
            ),
        )


class OpenAIDecisionEngine:
    def __init__(
        self,
        client: OpenAIResponsesClient,
        fallback: DecisionEngine | None = None,
    ) -> None:
        self.client = client
        self.fallback = fallback or RuleBasedDecisionEngine()

    @property
    def label(self) -> str:
        return f"openai:{self.client.model}"

    def decide(self, context: DecisionContext) -> AgentDecision:
        try:
            payload = build_openai_decision_payload(self.client.model, context)
            response = self.client.create_response(payload)
            return parse_openai_decision_response(response, context)
        except Exception as exc:
            fallback_decision = self.fallback.decide(context)
            fallback_decision.message = (
                f"OpenAI decision engine fell back to {self.fallback.label}: {exc}\n"
                f"{fallback_decision.message}"
            )
            return fallback_decision


def build_default_decision_engine() -> DecisionEngine:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return RuleBasedDecisionEngine()

    model = os.getenv("AUTOPATCH_OPENAI_MODEL", "gpt-5.4-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    client = OpenAIResponsesClient(api_key=api_key, model=model, base_url=base_url)
    return OpenAIDecisionEngine(client=client)


def build_openai_decision_payload(model: str, context: DecisionContext) -> dict[str, object]:
    return {
        "model": model,
        "instructions": DECISION_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": render_decision_prompt(context),
                    }
                ],
            }
        ],
        "tools": [SCAN_JAVA_TOOL],
        "tool_choice": "auto",
    }


def parse_openai_decision_response(
    response: dict[str, object],
    context: DecisionContext,
) -> AgentDecision:
    output = response.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call" and item.get("name") == "scan_java":
                arguments = parse_tool_arguments(item.get("arguments"))
                scope = arguments.get("scope", context.scoped_paths or ["."])
                if not isinstance(scope, list):
                    scope = context.scoped_paths or ["."]
                return AgentDecision(
                    action="tool_call",
                    message="OpenAI chose to call scan_java.",
                    tool_name="scan_java",
                    tool_args={"scope": [str(item) for item in scope]},
                )

    text = extract_response_text(response)
    return AgentDecision(
        action="respond",
        message=text or "OpenAI returned no tool call and no text response.",
    )


def parse_tool_arguments(arguments: object) -> dict[str, object]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


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


def render_decision_prompt(context: DecisionContext) -> str:
    scoped_paths = ", ".join(context.scoped_paths) if context.scoped_paths else "(none)"
    return (
        f"User text:\n{context.user_text}\n\n"
        f"Scoped paths:\n{scoped_paths}\n\n"
        f"Has active findings:\n{context.has_active_findings}\n"
    )


DECISION_INSTRUCTIONS = (
    "You are AutoPatch-J's decision engine. "
    "Decide whether the local scan_java tool should be called. "
    "Call scan_java when the user asks to scan for issues, vulnerabilities, or code problems. "
    "If scoped paths are provided, keep the tool call limited to those paths. "
    "If no tool call is needed, reply with a concise plain-text response."
)


SCAN_JAVA_TOOL = {
    "type": "function",
    "name": "scan_java",
    "description": "Run the local Java scanner on the selected repository scope.",
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repository-relative file or directory paths to scan.",
            }
        },
        "required": ["scope"],
        "additionalProperties": False,
    },
}
