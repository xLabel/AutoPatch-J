from __future__ import annotations

from autopatch_j.agent.progress_guard import (
    ReactProgressGuard,
    build_react_step_trace,
    normalize_observation_summary,
    normalize_tool_arguments,
)
from autopatch_j.llm.dialect import ToolCall
from autopatch_j.tools.base import ToolResult


def _trace(
    tool_name: str,
    arguments: dict,
    status: str,
    summary: str,
):
    return build_react_step_trace(
        ToolCall(name=tool_name, arguments=arguments, call_id="call-1"),
        ToolResult(status=status, message="message", summary=summary),
    )


def test_normalize_tool_arguments_ignores_json_field_order() -> None:
    left = normalize_tool_arguments({"path": "src\\Demo.java", "line": 12})
    right = normalize_tool_arguments({"line": 12, "path": "src\\Demo.java"})

    assert left == right


def test_normalize_observation_summary_collapses_whitespace_and_paths() -> None:
    summary = normalize_observation_summary(" 已读取源代码: src\\Demo.java \n\n 第 12 行 ")

    assert summary == "已读取源代码: src/Demo.java 第 12 行"


def test_progress_guard_blocks_three_identical_traces() -> None:
    guard = ReactProgressGuard()
    trace = _trace("read_source_code", {"path": "src/Demo.java"}, "ok", "已读取源代码: src/Demo.java")

    assert not guard.record(trace).blocked
    assert not guard.record(trace).blocked
    result = guard.record(trace)

    assert result.blocked
    assert "连续 3 次重复" in result.reason


def test_progress_guard_blocks_failed_short_cycle() -> None:
    guard = ReactProgressGuard()
    first = _trace("read_source_code", {"path": "missing.java"}, "error", "读取失败: missing.java")
    second = _trace("search_symbols", {"query": "Missing"}, "error", "未找到符号: Missing")

    assert not guard.record(first).blocked
    assert not guard.record(second).blocked
    assert not guard.record(first).blocked
    result = guard.record(second)

    assert result.blocked
    assert "短周期" in result.reason


def test_progress_guard_does_not_block_ok_short_cycle() -> None:
    guard = ReactProgressGuard()
    first = _trace("get_finding_detail", {"finding_id": "F1"}, "ok", "已获取 finding 详情: F1")
    second = _trace("read_source_code", {"path": "src/Demo.java"}, "ok", "已读取源代码: src/Demo.java")

    guard.record(first)
    guard.record(second)
    guard.record(first)
    result = guard.record(second)

    assert not result.blocked
