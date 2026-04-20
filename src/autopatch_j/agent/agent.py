from __future__ import annotations

from typing import Protocol

from autopatch_j.agent.prompts import ACTION_TOOLS, build_agent_messages
from autopatch_j.agent.types import AgentAction, AgentContext, AgentResult
from autopatch_j.llm import LLM, LLMResponse, build_default_llm
from autopatch_j.llm_config import missing_llm_config_message
from autopatch_j.tools.base import ToolName


class AutoPatchAgent(Protocol):
    def chat(self, context: AgentContext) -> AgentResult:
        """Run one user turn and return the next observable agent result."""

    @property
    def label(self) -> str:
        """A short label for status output."""


class UnavailableAgent:
    @property
    def label(self) -> str:
        return "llm:unavailable"

    def chat(self, context: AgentContext) -> AgentResult:
        del context
        return AgentResult(
            action=AgentAction.ANSWER,
            message=missing_llm_config_message("自然语言 Agent"),
        )


class LLMAgent:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    @property
    def label(self) -> str:
        return self.llm.label

    def chat(self, context: AgentContext) -> AgentResult:
        try:
            response = self.llm.chat(
                messages=build_agent_messages(context),
                tools=ACTION_TOOLS,
                tool_choice="auto",
                stream=True,
                on_token=context.on_answer_delta,
            )
            return parse_agent_response(response, context)
        except Exception as exc:
            return AgentResult(
                action=AgentAction.ANSWER,
                message=f"LLM agent failed after retries: {exc}",
            )


def build_default_agent() -> AutoPatchAgent:
    llm = build_default_llm()
    if llm is None:
        return UnavailableAgent()
    return LLMAgent(llm=llm)


def parse_agent_response(response: LLMResponse, context: AgentContext) -> AgentResult:
    for tool_call in response.tool_calls:
        if tool_call.name == "scan":
            scope = tool_call.arguments.get("scope", context.scoped_paths or ["."])
            if not isinstance(scope, list):
                scope = context.scoped_paths or ["."]
            return AgentResult(
                action=AgentAction.SCAN,
                message="Agent chose to run the Java scanner.",
                tool_name=ToolName.SCAN,
                tool_args={"scope": [str(item) for item in scope]},
            )
        if tool_call.name == "patch":
            return AgentResult(
                action=AgentAction.PATCH,
                message="Agent chose to generate a patch.",
                tool_args=dict(tool_call.arguments),
            )

    return AgentResult(
        action=AgentAction.ANSWER,
        message=response.content or "Agent returned no action.",
        streamed=bool(response.content and context.on_answer_delta),
    )
