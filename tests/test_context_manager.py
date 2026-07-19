from __future__ import annotations

from unittest.mock import MagicMock

from autopatch_j.agent.context_manager import (
    ContextPressure,
    ContextUsage,
    RequestContextManager,
    RequestContextBudget,
    is_context_overflow_error,
    required_compaction_reclaim,
)
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.llm.context_window import ModelContextProfile, resolve_context_profile


def test_default_1m_budget_uses_large_history_and_bounded_recall() -> None:
    budget = RequestContextBudget.from_profile(
        resolve_context_profile(model="deepseek-v4-flash")
    )

    assert budget.input_capacity == 950_848
    assert budget.recent_history_tokens == int(950_848 * 0.40)
    assert budget.recent_tail_tokens == 128 * 1024
    assert budget.checkpoint_tokens == 16 * 1024
    assert budget.durable_recall_tokens == 24 * 1024
    assert budget.memory_map_tokens == 8 * 1024


def test_context_pressure_has_deterministic_boundaries() -> None:
    budget = RequestContextBudget.from_profile(
        resolve_context_profile(model="deepseek-v4-flash")
    )

    assert budget.pressure_for(budget.soft_pressure_tokens) is ContextPressure.NORMAL
    assert (
        budget.pressure_for(budget.soft_pressure_tokens + 1)
        is ContextPressure.PRUNE_TOOLS
    )
    assert (
        budget.pressure_for(budget.compaction_pressure_tokens + 1)
        is ContextPressure.COMPACT
    )
    assert (
        budget.pressure_for(budget.input_capacity + 1)
        is ContextPressure.OVERFLOW
    )


def test_context_usage_separates_session_and_durable_memory() -> None:
    usage = ContextUsage(
        checkpoint_tokens=10,
        recent_history_tokens=20,
        memory_map_tokens=30,
        memory_detail_tokens=40,
        current_input_tokens=50,
    )

    assert usage.session_continuity_tokens == 30
    assert usage.durable_recall_tokens == 70
    assert usage.total_tokens == 150


def test_compaction_reclaim_requires_absolute_or_relative_progress() -> None:
    assert required_compaction_reclaim(100_000) == 32 * 1024
    assert required_compaction_reclaim(900_000) == 90_000


def test_context_manager_prunes_replayable_tool_without_mutating_trace() -> None:
    catalog = MagicMock()
    adapter = AgentMessageAdapter(catalog)
    manager = RequestContextManager(
        ModelContextProfile(
            model="test",
            context_window=100_000,
            max_output_tokens=1_000,
            provider_safety_tokens=1_000,
        ),
        adapter,
    )
    full_observation = "x" * 240_000
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_source_file",
                        "arguments": '{"path":"A.java"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_source_file",
            "content": full_observation,
            "tool_status": "ok",
            "tool_summary": "A.java 已读取",
        },
        *({"role": "assistant", "content": f"step {index}"} for index in range(6)),
        {"role": "user", "content": "continue"},
    ]

    prepared = manager.prepare(
        messages=messages,
        system_prompt="system",
        tools=[],
        initial_history_count=0,
        checkpoint_builder=lambda *_: (_ for _ in ()).throw(
            AssertionError("pruning should be sufficient")
        ),
    )

    assert prepared.pruned_tool_results == 1
    assert prepared.compacted is False
    assert "observation 已卸载" in prepared.messages[3]["content"]
    assert messages[2]["content"] == full_observation
    assert prepared.messages[2]["tool_calls"][0]["id"] == "call-1"
    assert prepared.messages[3]["tool_call_id"] == "call-1"


def test_context_manager_builds_checkpoint_and_keeps_recent_tail() -> None:
    adapter = AgentMessageAdapter(MagicMock())
    manager = RequestContextManager(
        ModelContextProfile(
            model="test",
            context_window=200_000,
            max_output_tokens=1_000,
            provider_safety_tokens=1_000,
        ),
        adapter,
    )
    old_discourse = "old-fact " * 70_000
    messages = [
        {"role": "user", "content": old_discourse},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "keep this recent request"},
    ]
    calls: list[list[dict[str, object]]] = []

    prepared = manager.prepare(
        messages=messages,
        system_prompt="system",
        tools=[],
        initial_history_count=2,
        checkpoint_builder=lambda older, _previous: (
            calls.append(older) or "## Goal\ncontinue audit\n## Verified facts\nold-fact"
        ),
    )

    assert prepared.compacted is True
    assert len(calls) == 1
    assert "old-fact" in str(calls[0])
    assert "<runtime_checkpoint>" in prepared.messages[1]["content"]
    assert prepared.messages[-1]["content"] == "keep this recent request"
    assert old_discourse not in str(prepared.messages)


def test_repeated_runtime_compaction_never_drops_current_user_input() -> None:
    adapter = AgentMessageAdapter(MagicMock())
    manager = RequestContextManager(
        ModelContextProfile(
            model="test",
            context_window=200_000,
            max_output_tokens=1_000,
            provider_safety_tokens=1_000,
        ),
        adapter,
    )
    current_input = {"role": "user", "content": "audit this exact request"}
    messages = [
        current_input,
        *(
            {"role": "assistant", "content": f"step-{index} " + "x" * 90_000}
            for index in range(6)
        ),
    ]
    previous_checkpoints: list[str | None] = []
    checkpoint_inputs: list[list[dict[str, object]]] = []

    def build_checkpoint(
        older: list[dict[str, object]],
        previous: str | None,
    ) -> str:
        checkpoint_inputs.append(older)
        previous_checkpoints.append(previous)
        return f"checkpoint-{len(previous_checkpoints)}"

    first = manager.prepare(
        messages=messages,
        system_prompt="system",
        tools=[],
        initial_history_count=0,
        checkpoint_builder=build_checkpoint,
    )
    messages.extend(
        {"role": "assistant", "content": f"later-{index} " + "y" * 90_000}
        for index in range(6)
    )
    second = manager.prepare(
        messages=messages,
        system_prompt="system",
        tools=[],
        initial_history_count=0,
        checkpoint_builder=build_checkpoint,
    )

    assert first.compacted is True
    assert second.compacted is True
    assert previous_checkpoints == [None, "checkpoint-1"]
    assert all(current_input not in older for older in checkpoint_inputs)
    assert sum(
        message.get("content") == current_input["content"]
        for message in second.messages
    ) == 1
    assert second.usage.current_input_tokens > 0


def test_thread_checkpoint_and_memory_map_use_separate_budgets() -> None:
    adapter = AgentMessageAdapter(MagicMock())
    manager = RequestContextManager(
        ModelContextProfile(
            model="test",
            context_window=100_000,
            max_output_tokens=1_000,
            provider_safety_tokens=1_000,
        ),
        adapter,
    )

    prepared = manager.prepare(
        messages=[{"role": "user", "content": "current request"}],
        system_prompt="system",
        tools=[],
        initial_history_count=0,
        checkpoint_builder=lambda *_: "unused",
        thread_checkpoint="older ordinary discussion",
        advisory_context="## Project Memory\n- prefer Java 21",
    )

    assert prepared.messages[-1]["content"] == "current request"
    assert any(
        str(message["content"]).startswith("<thread_checkpoint>")
        for message in prepared.messages
    )
    assert any(
        str(message["content"]).startswith("<memory_map>")
        for message in prepared.messages
    )
    assert prepared.usage.checkpoint_tokens > 0
    assert prepared.usage.memory_map_tokens > 0
    assert prepared.usage.current_input_tokens > 0
    assert prepared.usage.session_continuity_tokens == prepared.usage.checkpoint_tokens
    assert prepared.usage.durable_recall_tokens == prepared.usage.memory_map_tokens


def test_context_overflow_detection_is_specific() -> None:
    assert is_context_overflow_error(RuntimeError("context_length_exceeded"))
    assert is_context_overflow_error(RuntimeError("maximum context length is 1000"))
    assert not is_context_overflow_error(RuntimeError("rate limit exceeded"))
