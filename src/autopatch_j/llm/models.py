from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """标准 OpenAI function tool call 的内部表示。"""

    name: str
    arguments: dict[str, Any]
    call_id: str
    raw_arguments: str = ""


@dataclass(slots=True)
class LLMResponse:
    """
    LLM 响应的统一包装。

    content 是最终可见文本，tool_calls 是标准化后的工具调用，reasoning_content 保留供应商返回的思考链字段。
    response_id 与 finish_reason 只保留有界协议元数据，不包含响应正文。
    """

    content: str
    tool_calls: list[ToolCall] | None = None
    reasoning_content: str | None = None
    reasoning_content_present: bool = False
    response_id: str | None = None
    finish_reason: str | None = None
