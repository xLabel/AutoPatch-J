from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
import openai


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str
    raw_arguments: str = ""


@dataclass(slots=True)
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] | None = None
    reasoning_content: str | None = None


class LLMClient:
    """
    LLM 通信客户端 (Infrastructure Layer)
    职责：封装 OpenAI 兼容协议，处理流式响应与工具调用解析。
    """

    # 临时关闭阿里云百炼 DeepSeek 的 DSML 兼容逻辑。
    # 切回百炼网关时可重新打开。
    ENABLE_DSML_COMPAT = False

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    _DSML_MARKER_PATTERN = re.compile(r"<\s*[｜|]\s*DSML\s*[｜|]")
    _DSML_INVOKE_PATTERN = re.compile(
        r"<\s*[｜|]\s*DSML\s*[｜|]\s*invoke\s+name=\"(?P<name>[^\"]+)\">\s*(?P<params>.*?)\s*</\s*[｜|]\s*DSML\s*[｜|]\s*invoke>",
        re.DOTALL,
    )
    _DSML_PARAM_PATTERN = re.compile(
        r"<\s*[｜|]\s*DSML\s*[｜|]\s*parameter\s+name=\"(?P<key>[^\"]+)\"[^>]*>(?P<value>.*?)</\s*[｜|]\s*DSML\s*[｜|]\s*parameter>",
        re.DOTALL,
    )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        extra_body: dict[str, Any] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning_token: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """执行流式对话并解析响应"""
        
        # 针对百炼 DeepSeek 的特殊处理：必须在 body 中显式开启
        stream_options = None
        if extra_body and extra_body.get("enable_thinking"):
             stream_options = {"include_usage": True}

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=True,
            extra_body=extra_body,
            stream_options=stream_options
        )

        full_content = ""
        visible_content = ""
        full_reasoning = ""
        tool_calls_map: dict[int, dict[str, Any]] = {}
        stream_state = {"suppressed": False, "pending": ""} if self.ENABLE_DSML_COMPAT else None

        for chunk in response:
            if not chunk.choices:
                continue
            
            delta = chunk.choices[0].delta
            
            # 1. 解析思考链 (Reasoning)
            # 兼容不同厂商的字段名 (reasoning_content 或 reasoning)
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                full_reasoning += reasoning
                if on_reasoning_token:
                    on_reasoning_token(reasoning)

            # 2. 解析文本内容 (Content)
            if delta.content:
                full_content += delta.content
                visible_piece = (
                    self._consume_visible_text(delta.content, stream_state)
                    if self.ENABLE_DSML_COMPAT and stream_state is not None
                    else delta.content
                )
                if visible_piece:
                    visible_content += visible_piece
                    if on_token:
                        on_token(visible_piece)

            # 3. 解析标准工具调用 (Tool Calls)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    index = tc.index
                    if index not in tool_calls_map:
                        tool_calls_map[index] = {"id": tc.id, "name": "", "args": ""}
                    
                    if tc.function.name:
                        tool_calls_map[index]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_map[index]["args"] += tc.function.arguments

        # 刷新最后一段尚未输出的普通文本
        tail_piece = self._flush_visible_text(stream_state) if self.ENABLE_DSML_COMPAT and stream_state is not None else ""
        if tail_piece:
            visible_content += tail_piece
            if on_token:
                on_token(tail_piece)

        # 4. 🚀 工业级增强：处理非标 DSML XML 标签
        # 如果标准 tool_calls 为空，但 content 中包含 <｜DSML｜> 标签，执行正则提取
        final_tool_calls: list[ToolCall] = []
        
        # 先处理标准的
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

        # 兜底处理：正则提取 DSML 标签
        if self.ENABLE_DSML_COMPAT and not final_tool_calls and self._contains_dsml_markup(full_content):
            dsml_calls = self._parse_dsml_tags(full_content)
            final_tool_calls.extend(dsml_calls)

        final_content = self._strip_dsml_markup(full_content) if self.ENABLE_DSML_COMPAT and final_tool_calls else visible_content

        return LLMResponse(
            content=final_content,
            tool_calls=final_tool_calls if final_tool_calls else None,
            reasoning_content=full_reasoning if full_reasoning else None
        )

    def _contains_dsml_markup(self, text: str) -> bool:
        return self._DSML_MARKER_PATTERN.search(text) is not None

    def _strip_dsml_markup(self, text: str) -> str:
        match = self._DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

    def _consume_visible_text(self, chunk: str, state: dict[str, Any]) -> str:
        """
        在流式输出中隐藏 DeepSeek 的 DSML 标签。
        一旦检测到 <｜DSML｜，后续内容全部视为工具调用载荷并停止向用户透传。
        """
        if state["suppressed"]:
            return ""

        marker = "<｜DSML｜"
        combined = f"{state['pending']}{chunk}"
        marker_index = combined.find(marker)
        if marker_index >= 0:
            state["suppressed"] = True
            state["pending"] = ""
            return combined[:marker_index]

        keep = min(len(marker) - 1, len(combined))
        if len(combined) <= keep:
            state["pending"] = combined
            return ""

        emit = combined[:-keep]
        state["pending"] = combined[-keep:]
        return emit

    def _flush_visible_text(self, state: dict[str, Any]) -> str:
        if state["suppressed"]:
            state["pending"] = ""
            return ""
        pending = str(state["pending"])
        state["pending"] = ""
        return pending

    def _parse_dsml_tags(self, text: str) -> list[ToolCall]:
        """使用正则表达式从文本中提取 DeepSeek 特有的 DSML 工具调用标签"""
        calls = []

        for i, match in enumerate(self._DSML_INVOKE_PATTERN.finditer(text)):
            name = match.group("name")
            params_raw = match.group("params")
            arguments = {}
            
            # 提取每一个参数
            for p_match in self._DSML_PARAM_PATTERN.finditer(params_raw):
                val = p_match.group("value").strip()
                # 简单处理：如果是 true/false/数字，进行转换
                if val.lower() == "true": val = True
                elif val.lower() == "false": val = False
                elif val.isdigit(): val = int(val)
                arguments[p_match.group("key")] = val
            
            calls.append(ToolCall(
                name=name,
                arguments=arguments,
                call_id=f"dsml-{i}",
                raw_arguments=params_raw
            ))
        return calls


def build_default_llm_client() -> LLMClient | None:
    from autopatch_j.config import GlobalConfig
    if not GlobalConfig.llm_api_key:
        return None
    return LLMClient(
        api_key=GlobalConfig.llm_api_key,
        base_url=GlobalConfig.llm_base_url,
        model=GlobalConfig.llm_model
    )
