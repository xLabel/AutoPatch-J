from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
import openai

from .dialect import ToolCall, MessageDialect, StandardDialect, DeepSeekAliyunDialect


@dataclass(slots=True)
class LLMResponse:
    """大模型响应统一包装模型"""
    content: str
    tool_calls: list[ToolCall] | None = None
    reasoning_content: str | None = None


class LLMClient:
    """
    大模型网关与方言适配器 (LLM Gateway & Dialect Strategy)。
    核心职责：
    1. 封装标准的 OpenAI 兼容协议，处理大模型流式响应、思考链 (Reasoning) 与工具调用 (Tool Calls)。
    2. 利用策略模式 (Strategy Pattern) 动态挂载方言解析器 (MessageDialect)，
       将特定厂商的黑盒参数与专属标签（如百炼的 <｜DSML｜>）同核心业务逻辑彻底解耦。
    """

    def __init__(self, api_key: str, base_url: str, model: str, reasoning_effort: str | None = None, stream_dialect: str = "standard") -> None:
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.stream_dialect = stream_dialect

    def _create_dialect(self) -> MessageDialect:
        if self.stream_dialect == "bailian-dsml":
            return DeepSeekAliyunDialect()
        return StandardDialect()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning_token: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        stream_options = None
        if extra_body and (extra_body.get("enable_thinking") or "thinking" in extra_body):
             stream_options = {"include_usage": True}

        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "extra_body": extra_body,
            "stream_options": stream_options
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort

        response = self.client.chat.completions.create(**kwargs)

        full_content = ""
        visible_content = ""
        full_reasoning = ""
        tool_calls_map: dict[int, dict[str, Any]] = {}
        dialect = self._create_dialect()

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
                if on_reasoning_token and reasoning:
                    on_reasoning_token(reasoning)

            if delta.content:
                full_content += delta.content
                visible_piece = dialect.consume_visible_text(delta.content)
                if visible_piece:
                    visible_content += visible_piece
                    if on_token:
                        on_token(visible_piece)

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
            if on_token:
                on_token(tail_piece)

        final_tool_calls: list[ToolCall] = []

        for tc_data in tool_calls_map.values():
            try:
                args = json.loads(tc_data["args"]) if tc_data["args"] else {}
                final_tool_calls.append(ToolCall(
                    name=tc_data["name"],
                    arguments=args,
                    call_id=tc_data["id"],
                    raw_arguments=tc_data["args"]
                ))
            except json.JSONDecodeError:
                continue

        if not final_tool_calls:
            final_tool_calls.extend(dialect.extract_tool_calls(full_content))

        final_content = dialect.strip_markup(full_content) if final_tool_calls else visible_content

        return LLMResponse(
            content=final_content,
            tool_calls=final_tool_calls if final_tool_calls else None,
            reasoning_content=full_reasoning if full_reasoning else None
        )


def build_default_llm_client() -> LLMClient | None:
    from autopatch_j.config import GlobalConfig
    if not GlobalConfig.llm_api_key:
        return None
    return LLMClient(
        api_key=GlobalConfig.llm_api_key,
        base_url=GlobalConfig.llm_base_url,
        model=GlobalConfig.llm_model,
        reasoning_effort=GlobalConfig.llm_reasoning_effort,
        stream_dialect=GlobalConfig.llm_stream_dialect
    )
