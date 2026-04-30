from __future__ import annotations

from unittest.mock import MagicMock

from autopatch_j.cli.stream_adapter import StreamAdapter


class _Workspace:
    def has_pending_patch(self) -> bool:
        return False


class _WorkspaceManager:
    def load_workspace(self) -> _Workspace:
        return _Workspace()


class _ChatFilter:
    def build_display_answer(self, user_text, answer, intent):
        return answer


class _Agent:
    def __init__(self) -> None:
        self.messages = []


def _build_stream_adapter(debug_mode: bool) -> StreamAdapter:
    return StreamAdapter(
        renderer=MagicMock(),
        workspace_manager=_WorkspaceManager(),
        chat_filter=_ChatFilter(),
        agent=_Agent(),
        describe_current_scope_paths=lambda: [],
        build_static_scan_summary=lambda: "",
        build_local_no_issue_summary=lambda: "",
        debug_mode=lambda: debug_mode,
    )


def test_stream_adapter_compacts_reasoning_and_observation_when_debug_is_off() -> None:
    stream = _build_stream_adapter(debug_mode=False)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_reasoning("reasoning one")
        on_reasoning("reasoning two")
        on_tool_start("read_source_code")
        on_observation("full source code", "已读取源代码: Demo.java")
        return ""

    stream.run(prompt="check", agent_call=agent_call)

    stream.renderer.print_reasoning_status.assert_called_once_with(0)
    stream.renderer.print_reasoning.assert_not_called()
    stream.renderer.print_observation.assert_not_called()
    stream.renderer.print_info.assert_any_call("已读取源代码: Demo.java")
    stream.renderer.print.assert_not_called()


def test_stream_adapter_expands_reasoning_and_observation_when_debug_is_on() -> None:
    stream = _build_stream_adapter(debug_mode=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_reasoning("full reasoning")
        on_observation("full observation", "compact summary")
        return ""

    stream.run(prompt="check", agent_call=agent_call)

    stream.renderer.print_info.assert_not_called()
    stream.renderer.print_reasoning.assert_called_once_with("full reasoning")
    stream.renderer.print_reasoning_status.assert_not_called()
    stream.renderer.print_observation.assert_called_once_with("full observation")
