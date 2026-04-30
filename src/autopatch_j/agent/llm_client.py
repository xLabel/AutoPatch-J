from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable
import openai

from .dialect import ToolCall, MessageDialect, StandardDialect, DeepSeekAliyunDialect


class LLMCallPurpose(Enum):
    """LLM 调用意图，业务层只声明用途，不传供应商参数。"""

    REACT = auto()
    CLASSIFIER = auto()


class LLMReasoningMode(Enum):
    """LLMClient 内部使用的 reasoning 策略。"""

    INHERIT = auto()
    DISABLED = auto()


@dataclass(frozen=True, slots=True)
class LLMRequestOptions:
    """由调用意图解析出的底层请求选项。"""

    stream: bool
    reasoning: LLMReasoningMode
    max_tokens: int | None = None
    temperature: float | None = None


_PURPOSE_OPTIONS: dict[LLMCallPurpose, LLMRequestOptions] = {
    LLMCallPurpose.REACT: LLMRequestOptions(
        stream=True,
        reasoning=LLMReasoningMode.INHERIT,
    ),
    LLMCallPurpose.CLASSIFIER: LLMRequestOptions(
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=128,
        temperature=0,
    ),
}


@dataclass(slots=True)
class LLMResponse:
    """
    LLM 响应的统一包装。

    content 是最终可见文本，tool_calls 是标准化后的工具调用，reasoning_content 保留供应商返回的思考链字段。
    """
    content: str
    tool_calls: list[ToolCall] | None = None
    reasoning_content: str | None = None


class LLMClient:
    """
    OpenAI 兼容 LLM 网关。

    职责边界：
    1. 封装聊天补全的流式调用，统一收集可见文本、reasoning 和 Tool Call。
    2. 通过 MessageDialect 兼容不同供应商的流式标签和工具调用格式。
    3. 不理解 AutoPatch-J 的业务意图；它只提供协议层输入输出。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str | None = None,
        stream_dialect: str = "standard",
    ) -> None:
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
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        options = self._resolve_options(purpose)
        kwargs = self._build_request_kwargs(
            messages=messages,
            tools=tools,
            options=options,
        )
        response = self._create_completion(kwargs=kwargs, options=options)
        if not options.stream:
            return self._parse_non_stream_response(response, on_content_delta=on_content_delta)

        return self._parse_stream_response(
            response,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
        )

    def _resolve_options(self, purpose: LLMCallPurpose) -> LLMRequestOptions:
        return _PURPOSE_OPTIONS[purpose]

    def _build_request_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: LLMRequestOptions,
    ) -> dict[str, Any]:
        extra_body = self._build_extra_body(options)
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": options.stream,
            "extra_body": extra_body,
        }
        if options.stream and extra_body and (extra_body.get("enable_thinking") or "thinking" in extra_body):
            kwargs["stream_options"] = {"include_usage": True}
        if options.reasoning is LLMReasoningMode.INHERIT and self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens
        if options.temperature is not None:
            kwargs["temperature"] = options.temperature
        return kwargs

    def _build_extra_body(self, options: LLMRequestOptions) -> dict[str, Any] | None:
        if options.reasoning is LLMReasoningMode.DISABLED:
            return {
                "thinking": {"type": "disabled"},
                "enable_thinking": False,
            }

        extra_body = self._load_global_extra_body()
        return extra_body or None

    def _load_global_extra_body(self) -> dict[str, Any]:
        from autopatch_j.config import GlobalConfig

        try:
            parsed = json.loads(GlobalConfig.llm_extra_body)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _create_completion(self, kwargs: dict[str, Any], options: LLMRequestOptions) -> Any:
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not self._should_retry_without_disabled_reasoning(exc, kwargs, options):
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs["extra_body"] = None
            retry_kwargs.pop("stream_options", None)
            return self.client.chat.completions.create(**retry_kwargs)

    def _should_retry_without_disabled_reasoning(
        self,
        exc: Exception,
        kwargs: dict[str, Any],
        options: LLMRequestOptions,
    ) -> bool:
        if options.reasoning is not LLMReasoningMode.DISABLED:
            return False
        if not kwargs.get("extra_body"):
            return False
        message = str(exc).lower()
        retry_markers = (
            "thinking",
            "enable_thinking",
            "extra_body",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized",
            "invalid parameter",
        )
        return any(marker in message for marker in retry_markers)

    def _parse_stream_response(
        self,
        response: Any,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
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

        if not final_tool_calls:
            final_tool_calls.extend(dialect.extract_tool_calls(full_content))

        final_content = dialect.strip_markup(full_content) if final_tool_calls else visible_content

        return LLMResponse(
            content=final_content,
            tool_calls=final_tool_calls if final_tool_calls else None,
            reasoning_content=full_reasoning if full_reasoning else None,
        )

    def _parse_non_stream_response(
        self,
        response: Any,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        if not response.choices:
            return LLMResponse(content="")

        message = response.choices[0].message
        content = message.content or ""
        dialect = self._create_dialect()
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


def build_default_llm_client() -> LLMClient | None:
    from autopatch_j.config import GlobalConfig
    if not GlobalConfig.llm_api_key:
        return None
    return LLMClient(
        api_key=GlobalConfig.llm_api_key,
        base_url=GlobalConfig.llm_base_url,
        model=GlobalConfig.llm_model,
        reasoning_effort=GlobalConfig.llm_reasoning_effort,
        stream_dialect=GlobalConfig.llm_stream_dialect,
    )
