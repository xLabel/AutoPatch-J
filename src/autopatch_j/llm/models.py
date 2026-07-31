from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.llm.dialects import ToolCall


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
    response_id: str | None = None
    finish_reason: str | None = None
