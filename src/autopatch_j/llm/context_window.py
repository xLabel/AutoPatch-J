from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from typing import Any


DEEPSEEK_CONTEXT_WINDOW = 1_000_000
DEEPSEEK_PROVIDER_MAX_OUTPUT_TOKENS = 384_000
DEEPSEEK_INPUT_CAPACITY = 840_000
DEEPSEEK_OPERATION_MAX_OUTPUT_TOKENS = 128_000
DEEPSEEK_PROVIDER_SAFETY_TOKENS = 32_000
MIN_PROVIDER_SAFETY_TOKENS = 16_384
MESSAGE_OVERHEAD_TOKENS = 6
HEAD_TAIL_OMISSION_MARKER = "\n… [中间内容已按 input capacity 省略] …\n"


class ContextCapacityError(RuntimeError):
    """请求中的必需组件无法在当前模型容量内安全装配。"""


@dataclass(frozen=True, slots=True)
class ModelContextProfile:
    model: str
    context_window: int
    provider_max_output_tokens: int
    input_capacity: int
    max_output_tokens: int
    provider_safety_tokens: int


_KNOWN_MODEL_PROFILES: dict[str, ModelContextProfile] = {
    model: ModelContextProfile(
        model=model,
        context_window=DEEPSEEK_CONTEXT_WINDOW,
        provider_max_output_tokens=DEEPSEEK_PROVIDER_MAX_OUTPUT_TOKENS,
        input_capacity=DEEPSEEK_INPUT_CAPACITY,
        max_output_tokens=DEEPSEEK_OPERATION_MAX_OUTPUT_TOKENS,
        provider_safety_tokens=DEEPSEEK_PROVIDER_SAFETY_TOKENS,
    )
    for model in ("deepseek-v4-flash", "deepseek-v4-pro")
}


def resolve_context_profile(
    *,
    model: str,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> ModelContextProfile:
    normalized_model = model.strip()
    known = _KNOWN_MODEL_PROFILES.get(normalized_model)
    if known is not None:
        if context_window is not None or max_output_tokens is not None:
            raise ValueError("内置 DeepSeek V4 profile 不接受 context 容量覆盖")
        return ModelContextProfile(
            model=model,
            context_window=known.context_window,
            provider_max_output_tokens=known.provider_max_output_tokens,
            input_capacity=known.input_capacity,
            max_output_tokens=known.max_output_tokens,
            provider_safety_tokens=known.provider_safety_tokens,
        )
    if context_window is None or max_output_tokens is None:
        raise ValueError(
            "未知模型缺少 context profile；请同时设置 "
            "AUTOPATCH_LLM_CONTEXT_WINDOW 和 AUTOPATCH_LLM_MAX_OUTPUT_TOKENS"
        )
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
        provider_max_output_tokens=max_output_tokens,
        input_capacity=context_window - max_output_tokens - provider_safety_tokens,
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
