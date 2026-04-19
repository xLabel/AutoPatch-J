from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from autopatch_j.intent import has_scan_intent


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


class RuleBasedDecisionEngine:
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
