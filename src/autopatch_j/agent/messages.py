from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """
    Agent 内部消息记录。

    ReAct 主循环只追加这种本地记录；发给供应商的 wire format 由
    AgentMessageAdapter 统一转换，避免业务流程直接依赖 OpenAI 兼容字段细节。
    """

    role: str
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    tool_status: str | None = None
    tool_summary: str | None = None
    tool_payload: Any | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> AgentMessage:
        return cls(
            role=str(record.get("role", "")),
            content=str(record.get("content", "")),
            name=record.get("name"),
            tool_call_id=record.get("tool_call_id"),
            tool_calls=record.get("tool_calls"),
            reasoning_content=record.get("reasoning_content"),
            tool_status=record.get("tool_status"),
            tool_summary=record.get("tool_summary"),
            tool_payload=record.get("tool_payload"),
        )

    @classmethod
    def user(cls, content: str) -> AgentMessage:
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
        reasoning_content: str | None,
    ) -> AgentMessage:
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

    @classmethod
    def tool(
        cls,
        *,
        tool_call_id: str,
        name: str,
        content: str,
        status: str | None,
        summary: str | None,
        payload: Any | None,
    ) -> AgentMessage:
        return cls(
            role="tool",
            tool_call_id=tool_call_id,
            name=name,
            content=content,
            tool_status=status,
            tool_summary=summary,
            tool_payload=payload,
        )

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls is not None:
            record["tool_calls"] = self.tool_calls
        if self.reasoning_content is not None:
            record["reasoning_content"] = self.reasoning_content
        if self.tool_call_id is not None:
            record["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            record["name"] = self.name
        if self.tool_status is not None:
            record["tool_status"] = self.tool_status
        if self.tool_summary is not None:
            record["tool_summary"] = self.tool_summary
        if self.tool_payload is not None:
            record["tool_payload"] = self.tool_payload
        return record
