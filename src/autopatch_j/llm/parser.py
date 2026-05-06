from __future__ import annotations

import json
from typing import Any, Callable

from autopatch_j.llm.dialect import MessageDialect, ToolCall
from autopatch_j.llm.models import LLMResponse

DialectFactory = Callable[[], MessageDialect]
TokenCallback = Callable[[str], None]


class LLMResponseParser:
    """
    OpenAI 兼容响应解析器。

    负责收集流式文本、reasoning 字段和 tool call，并通过 MessageDialect 兼容供应商私有标记。
    """

    def __init__(self, dialect_factory: DialectFactory) -> None:
        self.dialect_factory = dialect_factory

    def parse_stream_response(
        self,
        response: Any,
        on_content_delta: TokenCallback | None = None,
        on_reasoning_delta: TokenCallback | None = None,
    ) -> LLMResponse:
        full_content = ""
        visible_content = ""
        full_reasoning = ""
        tool_calls_map: dict[int, dict[str, Any]] = {}
        dialect = self.dialect_factory()

        for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 兼容不同厂商的思考链字段名 (reasoning_content 或 reasoning)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is None:
                reasoning = getattr(delta, "reasoning", None)

            if reasoning is not None:
                full_reasoning += reasoning
                if on_reasoning_delta and reasoning:
                    on_reasoning_delta(reasoning)

            if delta.content:
                full_content += delta.content
                visible_piece = dialect.consume_visible_text(delta.content)
                if visible_piece:
                    visible_content += visible_piece
                    if on_content_delta:
                        on_content_delta(visible_piece)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    index = tc.index
                    if index not in tool_calls_map:
                        tool_calls_map[index] = {"id": tc.id, "name": "", "args": ""}

                    if tc.function.name:
                        tool_calls_map[index]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_map[index]["args"] += tc.function.arguments

        tail_piece = dialect.flush_visible_text()
        if tail_piece:
            visible_content += tail_piece
            if on_content_delta:
                on_content_delta(tail_piece)

        final_tool_calls = self._parse_stream_tool_calls(tool_calls_map)

        if not final_tool_calls:
            final_tool_calls.extend(dialect.extract_tool_calls(full_content))

        final_content = dialect.strip_markup(full_content) if final_tool_calls else visible_content

        return LLMResponse(
            content=final_content,
            tool_calls=final_tool_calls if final_tool_calls else None,
            reasoning_content=full_reasoning if full_reasoning else None,
        )

    def parse_non_stream_response(
        self,
        response: Any,
        on_content_delta: TokenCallback | None = None,
    ) -> LLMResponse:
        if not response.choices:
            return LLMResponse(content="")

        message = response.choices[0].message
        content = message.content or ""
        dialect = self.dialect_factory()
        visible_content = dialect.strip_markup(content)
        if on_content_delta and visible_content:
            on_content_delta(visible_content)

        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is None:
            reasoning = getattr(message, "reasoning", None)

        final_tool_calls: list[ToolCall] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = tool_call.function
            raw_arguments = function.arguments or ""
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                continue
            final_tool_calls.append(
                ToolCall(
                    name=function.name,
                    arguments=arguments,
                    call_id=tool_call.id,
                    raw_arguments=raw_arguments,
                )
            )

        if not final_tool_calls:
            final_tool_calls.extend(dialect.extract_tool_calls(content))

        return LLMResponse(
            content=visible_content,
            tool_calls=final_tool_calls if final_tool_calls else None,
            reasoning_content=reasoning if reasoning else None,
        )

    def _parse_stream_tool_calls(self, tool_calls_map: dict[int, dict[str, Any]]) -> list[ToolCall]:
        final_tool_calls: list[ToolCall] = []
        for tc_data in tool_calls_map.values():
            try:
                args = json.loads(tc_data["args"]) if tc_data["args"] else {}
                final_tool_calls.append(
                    ToolCall(
                        name=tc_data["name"],
                        arguments=args,
                        call_id=tc_data["id"],
                        raw_arguments=tc_data["args"],
                    )
                )
            except json.JSONDecodeError:
                continue
        return final_tool_calls
