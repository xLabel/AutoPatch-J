from __future__ import annotations

from unittest.mock import MagicMock

from autopatch_j.cli.stream_adapter import StreamAdapter
from autopatch_j.core.models import IntentType


class _Workspace:
    def __init__(self, has_pending_patch: bool = False) -> None:
        self._has_pending_patch = has_pending_patch

    def has_pending_patch(self) -> bool:
        return self._has_pending_patch


class _WorkspaceManager:
    def __init__(self, has_pending_patch: bool = False) -> None:
        self.workspace = _Workspace(has_pending_patch)

    def load_workspace(self) -> _Workspace:
        return self.workspace


class _ChatFilter:
    def build_display_answer(self, user_text, answer, intent):
        return answer


class _Agent:
    def __init__(self) -> None:
        self.messages = []


def _build_stream_adapter(debug_mode: bool, has_pending_patch: bool = False) -> StreamAdapter:
    return StreamAdapter(
        renderer=MagicMock(),
        workspace_manager=_WorkspaceManager(has_pending_patch),
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
    stream.renderer.print_reasoning_text.assert_not_called()
    stream.renderer.print_agent_text.assert_any_call("已读取源代码: Demo.java")
    stream.renderer.print.assert_not_called()


def test_stream_adapter_expands_reasoning_and_observation_when_debug_is_on() -> None:
    stream = _build_stream_adapter(debug_mode=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_reasoning("full reasoning")
        on_observation("full observation", "compact summary")
        return ""

    stream.run(prompt="check", agent_call=agent_call)

    stream.renderer.print_agent_text.assert_called_once_with("full observation")
    stream.renderer.print_reasoning_text.assert_any_call("-- 深度思考中 --\n")
    stream.renderer.print_reasoning_text.assert_any_call("full reasoning")
    stream.renderer.print_reasoning_status.assert_not_called()
    stream.renderer.print.assert_not_called()


def test_stream_adapter_renders_patch_explain_answer_with_pending_patch() -> None:
    stream = _build_stream_adapter(debug_mode=False, has_pending_patch=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        return "patch explanation"

    stream.run(
        prompt="explain",
        agent_call=agent_call,
        answer_intent=IntentType.PATCH_EXPLAIN,
    )

    stream.renderer.print_agent_text.assert_called_once_with("patch explanation")
    stream.renderer.print.assert_not_called()


def test_stream_adapter_suppresses_audit_answer_with_pending_patch() -> None:
    stream = _build_stream_adapter(debug_mode=False, has_pending_patch=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        return "audit summary"

    stream.run(
        prompt="audit",
        agent_call=agent_call,
        answer_intent=IntentType.CODE_AUDIT,
    )

    stream.renderer.print_agent_text.assert_not_called()
    stream.renderer.print.assert_not_called()


def test_stream_adapter_respects_explicit_suppress_answer_output() -> None:
    stream = _build_stream_adapter(debug_mode=False, has_pending_patch=False)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        return "hidden answer"

    stream.run(
        prompt="audit",
        agent_call=agent_call,
        answer_intent=IntentType.PATCH_EXPLAIN,
        suppress_answer_output=True,
    )

    stream.renderer.print_agent_text.assert_not_called()
    stream.renderer.print.assert_not_called()


def test_stream_adapter_renders_buffered_answer_as_plain_agent_text() -> None:
    stream = _build_stream_adapter(debug_mode=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_token('**补丁已提交** `MessageDigest.getInstance("SHA-256")`')
        return ""

    stream.run(prompt="audit", agent_call=agent_call)

    stream.renderer.print_agent_text.assert_called_once_with(
        '**补丁已提交** `MessageDigest.getInstance("SHA-256")`',
    )
    stream.renderer.print.assert_not_called()


def test_stream_adapter_renders_buffered_plain_answer_with_newline() -> None:
    stream = _build_stream_adapter(debug_mode=True)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_token("补丁解释完成")
        return ""

    stream.run(
        prompt="explain patch",
        agent_call=agent_call,
        answer_intent=IntentType.PATCH_EXPLAIN,
        plain_answer=True,
    )

    stream.renderer.print_plain.assert_called_once_with("补丁解释完成")
    stream.renderer.print.assert_not_called()


def test_stream_adapter_drops_intermediate_answer_when_tool_call_starts() -> None:
    stream = _build_stream_adapter(debug_mode=False)

    def agent_call(prompt, on_token, on_reasoning, on_observation, on_tool_start):
        on_token("**F1 处理完成。**")
        on_tool_start("get_finding_detail")
        on_observation("full finding detail", "已获取 finding 详情: F2")
        return ""

    stream.run(prompt="audit", agent_call=agent_call)

    stream.renderer.print_agent_text.assert_called_once_with("已获取 finding 详情: F2")
    stream.renderer.print.assert_not_called()
