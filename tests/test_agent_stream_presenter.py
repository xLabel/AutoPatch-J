"""只注入回调事件与内存 renderer，验证用户看到的文本和折叠状态。"""

from __future__ import annotations

from unittest import mock

from autopatch_j.agent import result
from autopatch_j.cli import agent_stream_presenter
from autopatch_j.core.domain import intent as domain_intent


class _Workspace:
    def __init__(self, has_pending_patch: bool = False) -> None:
        self._has_pending_patch = has_pending_patch

    def has_pending_patch(self) -> bool:
        return self._has_pending_patch


def _agent_result(answer: str = "") -> result.AgentRunResult:
    return result.AgentRunResult(final_answer=answer, observations=())


def _build_agent_stream_presenter(
    debug_mode: bool,
    has_pending_patch: bool = False,
) -> tuple[agent_stream_presenter.AgentStreamPresenter, mock.MagicMock]:
    renderer = mock.MagicMock()
    stream = agent_stream_presenter.AgentStreamPresenter(
        renderer=renderer,
        workspace_manager=mock.MagicMock(
            load=lambda: _Workspace(has_pending_patch)
        ),
        chat_filter=mock.MagicMock(
            build_display_answer=lambda user_text, answer, intent: answer
        ),
        describe_current_scope_paths=lambda: [],
        build_static_scan_summary=lambda: "",
        build_local_no_issue_summary=lambda: "",
        debug_mode=lambda: debug_mode,
    )

    return stream, renderer


def test_stream_compacts_reasoning_and_observation_when_debug_is_off() -> None:
    stream, renderer = _build_agent_stream_presenter(debug_mode=False)

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_token
        on_reasoning("reasoning one")
        on_reasoning("reasoning two")
        on_tool_start("read_source_file")
        on_observation("full source code", "已读取源代码: Demo.java")
        return _agent_result()

    stream.run(prompt="check", agent_call=agent_call)

    renderer.print_reasoning_status.assert_called_once_with(0)
    renderer.print_reasoning_text.assert_not_called()
    renderer.print_agent_text.assert_any_call("已读取源代码: Demo.java")


def test_stream_expands_reasoning_and_observation_when_debug_is_on() -> None:
    stream, renderer = _build_agent_stream_presenter(debug_mode=True)

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_token, on_tool_start
        on_reasoning("full reasoning")
        on_observation("full observation", "compact summary")
        return _agent_result()

    stream.run(prompt="check", agent_call=agent_call)

    renderer.print_agent_text.assert_called_once_with("full observation")
    renderer.print_reasoning_text.assert_any_call("-- 深度思考中 --\n")
    renderer.print_reasoning_text.assert_any_call("full reasoning")
    renderer.print_reasoning_status.assert_not_called()


def test_stream_renders_patch_explain_answer_with_pending_patch() -> None:
    stream, renderer = _build_agent_stream_presenter(
        debug_mode=False, has_pending_patch=True
    )

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_token, on_reasoning, on_observation, on_tool_start
        return _agent_result("patch explanation")

    stream.run(
        prompt="explain",
        agent_call=agent_call,
        answer_intent=domain_intent.IntentType.PATCH_EXPLAIN,
    )

    renderer.print_agent_text.assert_called_once_with("patch explanation")


def test_stream_suppresses_audit_answer_with_pending_patch() -> None:
    stream, renderer = _build_agent_stream_presenter(
        debug_mode=False, has_pending_patch=True
    )

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_token, on_reasoning, on_observation, on_tool_start
        return _agent_result("audit summary")

    stream.run(
        prompt="audit",
        agent_call=agent_call,
        answer_intent=domain_intent.IntentType.CODE_AUDIT,
    )

    renderer.print_agent_text.assert_not_called()


def test_stream_respects_explicit_suppress_answer_output() -> None:
    stream, renderer = _build_agent_stream_presenter(
        debug_mode=False, has_pending_patch=False
    )

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_token, on_reasoning, on_observation, on_tool_start
        return _agent_result("hidden answer")

    stream.run(
        prompt="audit",
        agent_call=agent_call,
        answer_intent=domain_intent.IntentType.PATCH_EXPLAIN,
        suppress_answer_output=True,
    )

    renderer.print_agent_text.assert_not_called()


def test_stream_renders_buffered_answer_as_plain_agent_text() -> None:
    stream, renderer = _build_agent_stream_presenter(debug_mode=True)

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_reasoning, on_observation, on_tool_start
        on_token('**补丁已提交** `MessageDigest.getInstance("SHA-256")`')
        return _agent_result()

    stream.run(prompt="audit", agent_call=agent_call)

    renderer.print_agent_text.assert_called_once_with(
        '**补丁已提交** `MessageDigest.getInstance("SHA-256")`',
    )


def test_stream_renders_buffered_plain_answer_with_newline() -> None:
    stream, renderer = _build_agent_stream_presenter(debug_mode=True)

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_reasoning, on_observation, on_tool_start
        on_token("补丁解释完成")
        return _agent_result()

    stream.run(
        prompt="explain patch",
        agent_call=agent_call,
        answer_intent=domain_intent.IntentType.PATCH_EXPLAIN,
        plain_answer=True,
    )

    renderer.print_plain.assert_called_once_with("补丁解释完成")


def test_stream_drops_intermediate_answer_when_tool_call_starts() -> None:
    stream, renderer = _build_agent_stream_presenter(debug_mode=False)

    def agent_call(
        prompt, on_token, on_reasoning, on_observation, on_tool_start
    ):
        del prompt, on_reasoning
        on_token("**F1 处理完成。**")
        on_tool_start("get_finding_detail")
        on_observation("full finding detail", "已获取 finding 详情: F2")
        return _agent_result()

    stream.run(prompt="audit", agent_call=agent_call)

    renderer.print_agent_text.assert_called_once_with("已获取 finding 详情: F2")
