from __future__ import annotations

import pytest

from autopatch_j.llm.context_window import (
    estimate_messages_tokens,
    estimate_text_tokens,
    estimate_tools_tokens,
    resolve_context_profile,
)


def test_deepseek_profile_reserves_output_and_safety_margin() -> None:
    profile = resolve_context_profile(model="deepseek-v4-flash")

    assert profile.context_window == 1_000_000
    assert profile.max_output_tokens == 32_768
    assert profile.provider_safety_tokens == 16_384
    assert profile.input_capacity == 950_848


def test_unknown_profile_accepts_explicit_capacity() -> None:
    profile = resolve_context_profile(
        model="private-deepseek",
        context_window=1_000_000,
        max_output_tokens=20_000,
    )

    assert profile.input_capacity == 963_616


def test_invalid_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知模型"):
        resolve_context_profile(model="private")
    with pytest.raises(ValueError, match="必须大于"):
        resolve_context_profile(
            model="private",
            context_window=20_000,
            max_output_tokens=10_000,
        )


def test_token_estimator_counts_utf8_messages_and_tools() -> None:
    assert estimate_text_tokens("中文") == 2
    messages = [{"role": "user", "content": "解释 UserService"}]
    tools = [{"type": "function", "function": {"name": "memory_search"}}]

    assert estimate_messages_tokens(messages) > estimate_text_tokens("解释 UserService")
    assert estimate_tools_tokens(tools) > 0
