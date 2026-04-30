from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autopatch_j.agent.dialect import ToolCall
from autopatch_j.config import GlobalConfig
from autopatch_j.tools.base import Tool


class AgentMessageAdapter:
    """Convert local agent messages and tools into the LLM wire format."""

    def __init__(self, available_tools: Mapping[str, Tool]) -> None:
        self.available_tools = available_tools

    def dehydrate_history(
        self,
        messages: list[dict[str, Any]],
        current_system_prompt: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"role": "system", "content": current_system_prompt}]

        for i, message in enumerate(messages):
            new_message = self.fetch_llm_message(message)

            if message.get("role") == "tool":
                is_recent = i >= len(messages) - 5
                is_scan = message.get("name") == "scan_project"

                if not is_recent and not is_scan:
                    content = str(message.get("content", ""))
                    if len(content) > 200:
                        new_message["content"] = content[:100] + "\n... [已脱水压缩] ..."

            result.append(new_message)

        return result

    def fetch_llm_message(self, message: dict[str, Any]) -> dict[str, Any]:
        role = str(message.get("role", ""))
        if role == "assistant":
            llm_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content", ""),
            }
            if message.get("tool_calls") is not None:
                llm_message["tool_calls"] = message["tool_calls"]

            reasoning = message.get("reasoning_content")
            if reasoning is not None:
                llm_message["reasoning_content"] = reasoning
            elif GlobalConfig.llm_reasoning_effort or "thinking" in GlobalConfig.llm_extra_body:
                llm_message["reasoning_content"] = ""
            return llm_message

        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "content": message.get("content", ""),
            }

        return {
            "role": role,
            "content": message.get("content", ""),
        }

    def tool_schemas(self, allowed_tool_names: tuple[str, ...]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool_name in allowed_tool_names:
            tool = self.available_tools.get(tool_name)
            if tool is None:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return schemas

    def serialize_tool_calls(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        return [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments,
                },
            }
            for call in calls
        ]
