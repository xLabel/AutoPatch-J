from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    """
    LLM 工具调用的统一内部表示。

    不同供应商的 tool call 解析后都归一到该结构，供 Agent 调度工具执行。
    """
    name: str
    arguments: dict[str, Any]
    call_id: str
    raw_arguments: str = ""


class MessageDialect(Protocol):
    """
    供应商流式消息方言协议。

    实现类负责过滤可见文本、解析工具调用和剥离供应商标记。
    """
    def consume_visible_text(self, chunk: str) -> str: ...
    def flush_visible_text(self) -> str: ...
    def extract_tool_calls(self, full_content: str) -> list[ToolCall]: ...
    def strip_markup(self, full_content: str) -> str: ...


class StandardDialect:
    """
    标准 OpenAI tool_calls 方言。

    标准协议下无需从文本中额外解析工具调用，因此该实现只透传可见文本。
    """
    def consume_visible_text(self, chunk: str) -> str:
        return chunk

    def flush_visible_text(self) -> str:
        return ""

    def extract_tool_calls(self, full_content: str) -> list[ToolCall]:
        return []

    def strip_markup(self, full_content: str) -> str:
        return full_content
