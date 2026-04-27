from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    """大模型工具调用模型 (Domain Model)"""
    name: str
    arguments: dict[str, Any]
    call_id: str
    raw_arguments: str = ""


class MessageDialect(Protocol):
    """
    大模型方言解析策略接口。
    用于抹平不同厂商流式返回的特殊标签差异。
    """
    def consume_visible_text(self, chunk: str) -> str: ...
    def flush_visible_text(self) -> str: ...
    def extract_tool_calls(self, full_content: str) -> list[ToolCall]: ...
    def strip_markup(self, full_content: str) -> str: ...


class StandardDialect:
    """符合 OpenAI 标准的方言解析器"""
    def consume_visible_text(self, chunk: str) -> str:
        return chunk

    def flush_visible_text(self) -> str:
        return ""

    def extract_tool_calls(self, full_content: str) -> list[ToolCall]:
        return []

    def strip_markup(self, full_content: str) -> str:
        return full_content
