from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autopatch_j.agent.callbacks import AgentCallbacks
from autopatch_j.agent.context_manager import ContextCapacityError
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.agent.react_runner import ReActRunner, _split_compaction_text
from autopatch_j.llm.context_window import (
    ModelContextProfile,
    estimate_messages_tokens,
    estimate_text_tokens,
    resolve_context_profile,
)
from autopatch_j.llm.models import LLMResponse
from autopatch_j.llm.options import LLMCallPurpose


class OverflowLLM:
    def __init__(self, failures: int) -> None:
        self.context_profile = resolve_context_profile(model="deepseek-v4-flash")
        self.failures = failures
        self.react_calls = 0

    def chat(self, *, purpose: LLMCallPurpose, **_kwargs) -> LLMResponse:
        if purpose is LLMCallPurpose.CONTEXT_COMPACTION:
            return LLMResponse(content="## Goal\ncontinue")
        self.react_calls += 1
        if self.react_calls <= self.failures:
            raise RuntimeError("context_length_exceeded")
        return LLMResponse(content="done")


def _runner(llm: OverflowLLM) -> ReActRunner:
    catalog = MagicMock()
    catalog.schemas.return_value = []
    return ReActRunner(
        llm=llm,  # type: ignore[arg-type]
        message_adapter=AgentMessageAdapter(catalog),
        tool_executor=MagicMock(),
    )


def test_react_retries_provider_context_overflow_once() -> None:
    llm = OverflowLLM(failures=1)
    maps: list[str] = []

    result = _runner(llm).run(
        user_text="inspect",
        system_prompt="system",
        allowed_tool_names=(),
        callbacks=AgentCallbacks(),
        advisory_context_provider=lambda hard: (
            maps.append(f"map-{len(maps) + 1}-hard={hard}") or maps[-1]
        ),
    )

    assert result.final_answer == "done"
    assert llm.react_calls == 2
    assert maps == ["map-1-hard=False", "map-2-hard=True"]


def test_react_does_not_retry_context_overflow_a_third_time() -> None:
    llm = OverflowLLM(failures=2)

    with pytest.raises(RuntimeError, match="context_length_exceeded"):
        _runner(llm).run(
            user_text="inspect",
            system_prompt="system",
            allowed_tool_names=(),
            callbacks=AgentCallbacks(),
        )

    assert llm.react_calls == 2


class LocalCapacityLLM:
    def __init__(self) -> None:
        self.context_profile = ModelContextProfile(
            model="test",
            context_window=20_000,
            max_output_tokens=1_000,
            provider_safety_tokens=1_000,
        )
        self.react_calls = 0

    def chat(self, *, purpose: LLMCallPurpose, **_kwargs) -> LLMResponse:
        assert purpose is LLMCallPurpose.REACT
        self.react_calls += 1
        return LLMResponse(content="done")


def test_react_hard_rebuilds_local_capacity_error_before_provider() -> None:
    llm = LocalCapacityLLM()
    map_requests: list[bool] = []

    result = _runner(llm).run(  # type: ignore[arg-type]
        user_text="inspect",
        system_prompt="system",
        allowed_tool_names=(),
        callbacks=AgentCallbacks(),
        advisory_context_provider=lambda hard: (
            map_requests.append(hard) or ("" if hard else "x" * 60_000)
        ),
    )

    assert result.final_answer == "done"
    assert llm.react_calls == 1
    assert map_requests == [False, True]


def test_compaction_input_splits_oversized_unicode_without_loss() -> None:
    source = "用户约束：禁止三元表达式。" * 4_000

    fragments = _split_compaction_text(source, token_budget=1_000)

    assert "".join(fragments) == source
    assert len(fragments) > 1
    assert all(estimate_text_tokens(fragment) <= 1_000 for fragment in fragments)


class RecordingCompactionLLM:
    def __init__(self, *, context_window: int, max_output_tokens: int) -> None:
        self.context_profile = resolve_context_profile(
            model="custom-model",
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        )
        self.compaction_messages: list[list[dict[str, object]]] = []

    def chat(
        self,
        *,
        messages: list[dict[str, object]],
        purpose: LLMCallPurpose,
        **_kwargs: object,
    ) -> LLMResponse:
        assert purpose is LLMCallPurpose.CONTEXT_COMPACTION
        self.compaction_messages.append(messages)
        return LLMResponse(content="## Goal\n" + "继续" * 200)


def test_compaction_fragments_fit_small_input_capacity_without_loss() -> None:
    llm = RecordingCompactionLLM(context_window=20_000, max_output_tokens=1_000)
    older_messages = [{"role": "user", "content": "x" * 20_000}]

    checkpoint = _runner(llm)._build_runtime_checkpoint(older_messages, None)

    marker = "Older discourse fragment (serialized JSON):\n"
    fragments = [
        str(messages[1]["content"]).split(marker, 1)[1]
        for messages in llm.compaction_messages
    ]
    expected = json.dumps(
        older_messages,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert checkpoint
    assert "".join(fragments) == expected
    assert len(fragments) > 1
    assert all(
        estimate_messages_tokens(messages) <= llm.context_profile.input_capacity
        for messages in llm.compaction_messages
    )


def test_compaction_fails_before_provider_when_wrapper_does_not_fit() -> None:
    llm = RecordingCompactionLLM(context_window=16_600, max_output_tokens=100)

    with pytest.raises(ContextCapacityError, match="包装内容"):
        _runner(llm)._build_runtime_checkpoint(
            [{"role": "user", "content": "old discourse"}],
            None,
        )

    assert llm.compaction_messages == []
