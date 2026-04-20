from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from autopatch_j.llm import ChatCompletionClient, LLMResponse, build_default_llm_client


@dataclass(slots=True)
class DecisionContext:
    user_text: str
    scoped_paths: list[str]
    has_active_findings: bool
    mention_context: str = "(none)"


@dataclass(slots=True)
class AgentDecision:
    action: Literal["answer", "scan", "patch"]
    message: str
    tool_name: str | None = None
    tool_args: dict[str, object] = field(default_factory=dict)


class Planner(Protocol):
    def decide(self, context: DecisionContext) -> AgentDecision:
        """Return the next agent action for the current turn."""

    @property
    def label(self) -> str:
        """A short label for status output."""


class UnavailablePlanner:
    @property
    def label(self) -> str:
        return "llm:unavailable"

    def decide(self, context: DecisionContext) -> AgentDecision:
        del context
        return AgentDecision(
            action="answer",
            message=(
                "LLM planner is unavailable. Set LLM_API_KEY or OPENAI_API_KEY "
                "to enable natural-language agent actions."
            ),
        )


class LLMPlanner:
    def __init__(self, client: ChatCompletionClient) -> None:
        self.client = client

    @property
    def label(self) -> str:
        return self.client.label

    def decide(self, context: DecisionContext) -> AgentDecision:
        try:
            response = self.client.complete(
                messages=build_decision_messages(context),
                tools=AGENT_ACTION_TOOLS,
                tool_choice="auto",
                stream=True,
            )
            return parse_llm_decision_response(response, context)
        except Exception as exc:
            return AgentDecision(
                action="answer",
                message=f"LLM planner failed after retries: {exc}",
            )


def build_default_planner() -> Planner:
    client = build_default_llm_client()
    if client is None:
        return UnavailablePlanner()
    return LLMPlanner(client=client)


def build_decision_messages(context: DecisionContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": DECISION_INSTRUCTIONS},
        {"role": "user", "content": render_decision_prompt(context)},
    ]


def parse_llm_decision_response(
    response: LLMResponse | dict[str, object],
    context: DecisionContext,
) -> AgentDecision:
    if isinstance(response, dict):
        response = parse_legacy_response(response)

    for tool_call in response.tool_calls:
        if tool_call.name == "scan":
            scope = tool_call.arguments.get("scope", context.scoped_paths or ["."])
            if not isinstance(scope, list):
                scope = context.scoped_paths or ["."]
            return AgentDecision(
                action="scan",
                message="LLM planner chose to run the Java scanner.",
                tool_name="scan_java",
                tool_args={"scope": [str(item) for item in scope]},
            )
        if tool_call.name == "patch":
            return AgentDecision(
                action="patch",
                message="LLM planner chose to generate a patch.",
                tool_args=dict(tool_call.arguments),
            )

    return AgentDecision(
        action="answer",
        message=response.content or "LLM planner returned no action.",
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


def parse_legacy_response(response: dict[str, object]) -> LLMResponse:
    output = response.get("output", [])
    if not isinstance(output, list):
        return LLMResponse()

    texts: list[str] = []
    tool_calls = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            name = str(item.get("name", ""))
            raw_arguments = item.get("arguments")
            tool_calls.append(
                {
                    "function": {
                        "name": name,
                        "arguments": raw_arguments if isinstance(raw_arguments, str) else "",
                    }
                }
            )
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
    from autopatch_j.llm import parse_message_tool_calls

    return LLMResponse(
        content="\n".join(texts).strip(),
        tool_calls=parse_message_tool_calls(tool_calls),
    )


def render_decision_prompt(context: DecisionContext) -> str:
    scoped_paths = ", ".join(context.scoped_paths) if context.scoped_paths else "(none)"
    return (
        f"User text:\n{context.user_text}\n\n"
        f"Scoped paths:\n{scoped_paths}\n\n"
        f"Mention context:\n{context.mention_context}\n\n"
        f"Has active findings:\n{context.has_active_findings}\n"
    )


DECISION_INSTRUCTIONS = (
    "You are AutoPatch-J's planner. Choose one action for the current user turn. "
    "Call scan when the user asks to scan, inspect findings, or look for Java code problems. "
    "Call patch when the user asks to generate a patch from active findings or revise a pending patch. "
    "If scoped paths are provided, keep scan limited to those paths. "
    "If neither tool is needed, reply with concise plain text. "
    "Do not reveal chain-of-thought."
)


AGENT_ACTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scan",
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Draft one minimal patch from the active findings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "One-based finding index selected by the user, if specified.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
]


SCAN_JAVA_TOOL = {
    "type": "function",
    "function": AGENT_ACTION_TOOLS[0]["function"],
}
