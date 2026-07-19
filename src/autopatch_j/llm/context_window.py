from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from typing import Any


DEFAULT_CONTEXT_WINDOW = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
MIN_PROVIDER_SAFETY_TOKENS = 16_384
MESSAGE_OVERHEAD_TOKENS = 6


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

