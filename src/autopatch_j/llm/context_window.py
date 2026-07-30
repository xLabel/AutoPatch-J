from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from typing import Any


DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
MIN_PROVIDER_SAFETY_TOKENS = 16_384
MESSAGE_OVERHEAD_TOKENS = 6
HEAD_TAIL_OMISSION_MARKER = "\n… [中间内容已按 input capacity 省略] …\n"


class ContextCapacityError(RuntimeError):
    """请求中的必需组件无法在当前模型容量内安全装配。"""


@dataclass(frozen=True, slots=True)
class ModelContextProfile:
    model: str
    context_window: int
    max_output_tokens: int
    provider_safety_tokens: int

    @property
    def input_capacity(self) -> int:
        return (
            self.context_window
            - self.max_output_tokens
            - self.provider_safety_tokens
        )


_KNOWN_MODEL_PROFILES: dict[str, tuple[int, int]] = {
    "deepseek-v4-flash": (DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_OUTPUT_TOKENS),
}


def resolve_context_profile(
    *,
    model: str,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> ModelContextProfile:
    normalized_model = model.strip().lower()
    known = _KNOWN_MODEL_PROFILES.get(normalized_model)
    if context_window is None:
        if known is None:
            raise ValueError(
                "未知模型缺少 context window；请设置 "
                "AUTOPATCH_LLM_CONTEXT_WINDOW"
            )
        context_window = known[0]
    if max_output_tokens is None:
        max_output_tokens = known[1] if known is not None else DEFAULT_MAX_OUTPUT_TOKENS
    if context_window <= 0:
        raise ValueError("LLM context window 必须是正整数")
    if max_output_tokens <= 0:
        raise ValueError("LLM max output tokens 必须是正整数")

    provider_safety_tokens = max(
        MIN_PROVIDER_SAFETY_TOKENS,
        ceil(context_window * 0.01),
    )
    if max_output_tokens + provider_safety_tokens >= context_window:
        raise ValueError(
            "LLM context window 必须大于 max output tokens 与 provider safety margin 之和"
        )
    return ModelContextProfile(
        model=model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        provider_safety_tokens=provider_safety_tokens,
    )


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text.encode("utf-8")) / 3))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += MESSAGE_OVERHEAD_TOKENS
        total += estimate_text_tokens(str(message.get("role", "")))
        for key in (
            "content",
            "name",
            "tool_call_id",
            "reasoning_content",
        ):
            value = message.get(key)
            if value:
                total += estimate_text_tokens(str(value))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += estimate_text_tokens(
                json.dumps(tool_calls, ensure_ascii=False, sort_keys=True)
            )
    return total


def estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    return estimate_text_tokens(json.dumps(tools, ensure_ascii=False, sort_keys=True))


def estimate_request_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    return estimate_messages_tokens(messages) + estimate_tools_tokens(tools)


def clip_text_head_tail(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if estimate_text_tokens(text) <= max_tokens:
        return text
    marker_tokens = estimate_text_tokens(HEAD_TAIL_OMISSION_MARKER)
    if max_tokens <= marker_tokens:
        return _decode_utf8_prefix(text.encode("utf-8"), max_tokens * 3)
    content_bytes = (max_tokens - marker_tokens) * 3
    head_bytes = content_bytes // 2
    tail_bytes = content_bytes - head_bytes
    encoded = text.encode("utf-8")
    head = _decode_utf8_prefix(encoded, head_bytes)
    tail = _decode_utf8_suffix(encoded, tail_bytes)
    return f"{head}{HEAD_TAIL_OMISSION_MARKER}{tail}"


def fit_last_user_message(
    messages: list[dict[str, Any]],
    *,
    input_capacity: int,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    projected = [dict(message) for message in messages]
    if estimate_request_tokens(projected, tools) <= input_capacity:
        return projected
    user_index = next(
        (
            index
            for index in range(len(projected) - 1, -1, -1)
            if projected[index].get("role") == "user"
        ),
        None,
    )
    if user_index is None:
        raise ContextCapacityError("LLM 请求超过 input capacity，且没有可裁剪的 user message。")
    original = str(projected[user_index].get("content", ""))
    projected[user_index]["content"] = ""
    fixed_tokens = estimate_request_tokens(projected, tools)
    available_tokens = input_capacity - fixed_tokens
    if available_tokens <= 0:
        raise ContextCapacityError("LLM 请求固定内容超过 input capacity。")
    projected[user_index]["content"] = clip_text_head_tail(
        original,
        available_tokens,
    )
    if estimate_request_tokens(projected, tools) > input_capacity:
        raise ContextCapacityError("LLM user message 首尾投影后仍超过 input capacity。")
    return projected


def _decode_utf8_prefix(encoded: bytes, byte_limit: int) -> str:
    return encoded[: max(byte_limit, 0)].decode("utf-8", errors="ignore")


def _decode_utf8_suffix(encoded: bytes, byte_limit: int) -> str:
    if byte_limit <= 0:
        return ""
    return encoded[-byte_limit:].decode("utf-8", errors="ignore")
