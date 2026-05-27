from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from autopatch_j.agent.messages import AgentMessage
from autopatch_j.llm.dialects import ToolCall
from autopatch_j.config import GlobalConfig
from autopatch_j.tools.catalog import FunctionToolCatalog
from autopatch_j.tools.names import ToolNameLike


class AgentMessageAdapter:
    """
    Agent 本地消息与 LLM wire format 的适配器。

    职责边界：
    1. 把本地保存的 assistant/tool 消息清洗成 OpenAI 兼容消息结构。
    2. 压缩旧工具观察并保留 DeepSeek reasoning_content 兼容字段。
    3. 生成 tool schema 和序列化 Tool Call；不执行工具，也不改变 AgentSession 状态。
    """

    def __init__(self, tool_catalog: FunctionToolCatalog) -> None:
        self.tool_catalog = tool_catalog

    def dehydrate_history(
        self,
        messages: list[dict[str, Any]],
        current_system_prompt: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"role": "system", "content": current_system_prompt}]

        for i, message in enumerate(messages):
            agent_message = AgentMessage.from_record(message)
            new_message = self.fetch_llm_message(agent_message)

            if agent_message.role == "tool":
                is_recent = i >= len(messages) - 5
                is_scan = agent_message.name == "scan_project"

                if not is_recent and not is_scan:
                    content = agent_message.content
                    if len(content) > 200:
                        new_message["content"] = content[:100] + "\n... [已脱水压缩] ..."

            result.append(new_message)

        return result

    def fetch_llm_message(self, message: AgentMessage | dict[str, Any]) -> dict[str, Any]:
        agent_message = message if isinstance(message, AgentMessage) else AgentMessage.from_record(message)
        if agent_message.role == "assistant":
            llm_message: dict[str, Any] = {
                "role": "assistant",
                "content": agent_message.content,
            }
            if agent_message.tool_calls is not None:
                llm_message["tool_calls"] = agent_message.tool_calls

            reasoning = agent_message.reasoning_content
            if reasoning is not None:
                llm_message["reasoning_content"] = reasoning
            elif GlobalConfig.llm_reasoning_effort or "thinking" in GlobalConfig.llm_extra_body:
                llm_message["reasoning_content"] = ""
            return llm_message

        if agent_message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": agent_message.tool_call_id or "",
                "content": agent_message.content,
            }

        return {
            "role": agent_message.role,
            "content": agent_message.content,
        }

    def tool_schemas(self, allowed_tool_names: Iterable[ToolNameLike]) -> list[dict[str, Any]]:
        return self.tool_catalog.schemas(allowed_tool_names)

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
