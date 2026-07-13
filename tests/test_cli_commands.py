from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autopatch_j.cli.app import AutoPatchCli
from autopatch_j.cli.agent_stream_presenter import PresentedAgentResult
from autopatch_j.cli.commands import CLI_COMMANDS
from autopatch_j.cli.command_router import CommandRouter
from autopatch_j.agent.react_runner import AgentRunResult
from autopatch_j.config import GlobalConfig
from autopatch_j.core.memory import MemoryManager, MemorySchemaError, MemoryStatus
from autopatch_j.core.patching import SearchReplacePatchDraft
from autopatch_j.core.patching import SyntaxCheckResult
from autopatch_j.core.domain import (
    ReviewWorkspace,
    FindingTask,
    CodeScope,
    CodeScopeKind,
    ConversationRoute,
    IntentType,
    PatchDraftSnapshot,
    ReviewPatchItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.core.user_input import IntentClassificationResult, RouteClassificationResult


@pytest.fixture
def cli(tmp_path: Path) -> AutoPatchCli:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.renderer = MagicMock()
    cli_obj.command_router = CommandRouter(cli_obj.command_handlers, cli_obj.renderer)
    cli_obj.initialize_runtime(cli_obj.repo_root)
    return cli_obj


def _item(
    item_id: str,
    file_path: str = "src/main/java/demo/User.java",
    finding_id: str = "F1",
) -> ReviewPatchItem:
    return ReviewPatchItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=[finding_id],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftSnapshot(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff="diff",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale=f"fix {finding_id}",
            associated_finding_id=finding_id,
            source_scan_id="scan-1",
            target_check_id="demo.rule",
        ),
    )


def _patch_draft(new_string: str, finding_id: str | None = "F1") -> SearchReplacePatchDraft:
    return SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string=new_string,
        diff=f"diff {new_string}",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=f"fix {finding_id}",
        associated_finding_id=finding_id,
        source_scan_id="scan-1" if finding_id else None,
        target_check_id="demo.rule" if finding_id else None,
    )


def _finding(finding_id: str = "F1") -> FindingTask:
    return FindingTask(
        finding_id=finding_id,
        file_path="src/main/java/demo/User.java",
        check_id="demo.rule",
        start_line=1,
        end_line=1,
        message="demo finding",
        snippet="old",
    )


def _tool_message(status: str = "ok", finding_id: str = "F1") -> dict:
    return {
        "role": "tool",
        "name": "propose_patch",
        "tool_status": status,
        "tool_payload": {
            "associated_finding_id": finding_id,
            "file_path": "src/main/java/demo/User.java",
        },
    }


def _presented(
    trace_messages: list[dict] | None = None,
    display_answer: str = "",
) -> PresentedAgentResult:
    return PresentedAgentResult(
        raw_answer=display_answer,
        display_answer=display_answer,
        trace_messages=trace_messages or [],
    )


def _agent_result(answer: str = "", trace_messages: list[dict] | None = None) -> AgentRunResult:
    return AgentRunResult(final_answer=answer, trace_messages=trace_messages or [])


def test_handle_status_does_not_crash_with_pending_patch(cli: AutoPatchCli) -> None:
    # Setup workspace with a pending patch
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)

    # This should not raise AttributeError
    cli.command_handlers.handle_status()

    cli.renderer.print_panel.assert_called_once()


def test_handle_status_includes_output_mode(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_status()

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]

    assert "调试模式" in cells
    assert "关闭" in cells
    assert "LLM API Key" in cells
    assert "Semgrep" in cells
    assert "Tree-sitter" in cells
    assert "日志模式" not in cells
    assert "输出模式" not in cells
    assert "静态扫描器" not in cells


def test_handle_status_renders_without_runtime(cli: AutoPatchCli) -> None:
    cli.clear_runtime()

    cli.command_handlers.handle_status()

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]

    assert "工作区" in cells
    assert "未初始化" in cells
    assert "LLM API Key" in cells
    assert "Semgrep" in cells
    assert "Tree-sitter" in cells


def test_scanner_command_separates_active_and_planned_scanners(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_scanners()

    active_table = cli.renderer.print_table.call_args_list[0].args[0]
    active_cells = [str(cell) for column in active_table.columns for cell in column._cells]

    assert cli.renderer.print_table.call_count == 1
    assert active_table.title == "当前扫描器"
    assert "Semgrep" in active_cells
    cli.renderer.print_agent_text.assert_any_call("计划接入：SpotBugs、PMD、Checkstyle")


def test_clear_runtime_shuts_down_agent(cli: AutoPatchCli) -> None:
    assert cli.runtime is not None
    shutdown = MagicMock()
    cli.runtime.agent.shutdown = shutdown

    cli.clear_runtime()

    assert cli.runtime is None
    assert cli.input_router is None
    shutdown.assert_called_once_with(wait=False)


def test_doctor_command_is_removed(cli: AutoPatchCli) -> None:
    cli.command_router.handle_command("/doctor")

    cli.renderer.print_error.assert_called_once_with("未知命令：/doctor")


def test_command_router_parses_and_passes_shlex_arguments(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_memory = MagicMock()

    cli.command_router.handle_command('/memory show "memory item"')

    cli.command_handlers.handle_memory.assert_called_once_with(["show", "memory item"])


def test_command_router_rejects_arguments_for_argumentless_command(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_status = MagicMock()

    cli.command_router.handle_command("/status extra")

    cli.command_handlers.handle_status.assert_not_called()
    cli.renderer.print_error.assert_called_once_with("命令 /status 不接受参数")


def test_command_router_reports_invalid_shell_quoting(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_memory = MagicMock()

    cli.command_router.handle_command('/memory show "unterminated')

    cli.command_handlers.handle_memory.assert_not_called()
    assert "命令解析失败" in cli.renderer.print_error.call_args.args[0]


def test_help_uses_registered_command_descriptions(cli: AutoPatchCli) -> None:
    cli.command_handlers.handle_help()

    table = cli.renderer.print_table.call_args_list[0].args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]
    expected = [
        item
        for command in CLI_COMMANDS
        if command.show_in_help
        for item in (command.name, command.help_description)
    ]

    for item in expected:
        assert item in cells


def test_handle_patch_explain_does_not_crash(cli: AutoPatchCli) -> None:
    # Setup workspace with a pending patch
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.agent.perform_patch_explain = MagicMock(return_value=_agent_result())

    # This should not raise AttributeError
    cli.input_router.handle_patch_explain("explain this")

    cli.runtime.agent.perform_patch_explain.assert_called_once()


def test_handle_patch_revise_replaces_only_current_patch(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[
                "src/main/java/demo/User.java",
                "src/main/java/demo/UserService.java",
            ],
            is_locked=True,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    replacement = SearchReplacePatchDraft(
        file_path="src/main/java/demo/User.java",
        old_string="old",
        new_string="better",
        diff="better diff",
        validation=SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale="better fix",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_check_id="demo.rule",
    )

    def revise_current(*args, **kwargs):
        cli.runtime.agent.session.set_revised_patch_draft(replacement)
        return _agent_result("done")

    cli.runtime.agent.perform_patch_revise = MagicMock(side_effect=revise_current)

    cli.input_router.handle_patch_revise("rewrite current")

    updated = cli.runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.current_patch_index == 0
    assert updated.patch_items[0].draft.new_string == "better"
    assert updated.patch_items[1].file_path == "src/main/java/demo/UserService.java"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_agent_text.assert_any_call("已更新当前补丁，后续补丁保持不变。")


def test_handle_patch_revise_keeps_queue_when_no_revision_created(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[
            _item("item-1", "src/main/java/demo/User.java", "F1"),
            _item("item-2", "src/main/java/demo/UserService.java", "F2"),
        ],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.agent.perform_patch_revise = MagicMock(return_value=_agent_result("no revision"))

    cli.input_router.handle_patch_revise("explain only")

    updated = cli.runtime.workspace_manager.load()
    assert [item.item_id for item in updated.patch_items] == ["item-1", "item-2"]
    assert updated.patch_items[0].draft.rationale == "fix F1"
    assert updated.patch_items[1].draft.rationale == "fix F2"
    cli.renderer.print_agent_text.assert_any_call("未生成修订补丁，当前补丁保持不变。")


def test_process_single_finding_commits_staged_patch_after_success(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("better", "F1"))
        return _presented([_tool_message("ok", "F1")])

    cli.agent_runner.run = MagicMock(side_effect=run_agent)

    cli.input_router.code_audit_workflow._process_single_finding(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "better"
    assert finding.status.value == "patch_ready"
    assert cli.runtime.agent.session.proposed_patch_draft is None
    assert cli.agent_runner.run.call_args.kwargs["suppress_answer_output"] is True


def test_process_single_finding_does_not_commit_without_staged_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]
    cli.agent_runner.run = MagicMock(return_value=_presented([_tool_message("ok", "F1")]))

    cli.input_router.code_audit_workflow._process_single_finding(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"
    assert cli.agent_runner.run.call_args.kwargs["suppress_answer_output"] is True


def test_process_single_finding_rejects_patch_for_different_finding(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_agent(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("wrong-finding", "F2"))
        return _presented([_tool_message("ok", "F1")])

    cli.agent_runner.run = MagicMock(side_effect=run_agent)

    cli.input_router.code_audit_workflow._process_single_finding(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"
    assert finding.last_error_code == "NO_PROPOSED_PATCH_DRAFT"


def test_code_audit_stops_at_batch_limit(cli: AutoPatchCli, monkeypatch: pytest.MonkeyPatch) -> None:
    backlog = [_finding("F1"), _finding("F2"), _finding("F3")]
    workflow = cli.input_router.code_audit_workflow
    workflow._prepare_audit_workspace = MagicMock(return_value=backlog)  # type: ignore[method-assign]

    def mark_failed(finding: FindingTask, text: str, backlog: list[FindingTask]) -> None:
        cli.runtime.backlog_manager.mark_failed(backlog, finding.finding_id, None, None)

    workflow._process_single_finding = MagicMock(side_effect=mark_failed)  # type: ignore[method-assign]
    monkeypatch.setattr(GlobalConfig, "audit_batch_limit", 2)

    workflow.handle_code_audit("audit")

    assert workflow._process_single_finding.call_count == 2
    assert backlog[0].status.value == "failed"
    assert backlog[1].status.value == "failed"
    assert backlog[2].is_pending()
    cli.renderer.print_agent_text.assert_any_call(
        "本轮已处理 2 个 finding，仍有 1 个待处理。请确认当前补丁后再次发起检查继续处理。"
    )


def test_finding_retry_commits_only_retry_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]
    old_draft = _patch_draft("stale", "F1")
    cli.runtime.agent.session.set_proposed_patch_draft(old_draft)

    def run_retry(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("retry-fix", "F1"))
        return _presented([_tool_message("ok", "F1")])

    cli.agent_runner.run = MagicMock(side_effect=run_retry)

    cli.input_router.code_audit_workflow._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "retry-fix"
    assert finding.status.value == "patch_ready"


def test_finding_retry_failure_does_not_commit_patch(cli: AutoPatchCli) -> None:
    finding = _finding("F1")
    backlog = [finding]

    def run_retry(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("stale", "F1"))
        cli.runtime.agent.session.clear_proposed_patch_draft()
        return _presented([_tool_message("error", "F1")])

    cli.agent_runner.run = MagicMock(side_effect=run_retry)

    cli.input_router.code_audit_workflow._handle_finding_retry(finding, "audit", backlog)

    workspace = cli.runtime.workspace_manager.load()
    assert workspace.patch_items == []
    assert finding.status.value == "failed"


def test_zero_finding_review_commits_staged_patch(cli: AutoPatchCli) -> None:
    scope = CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=[],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )

    def run_agent(*args, **kwargs):
        cli.runtime.agent.session.set_proposed_patch_draft(_patch_draft("zero-fix", None))
        return _presented([_tool_message("ok", "F1")])

    cli.agent_runner.run = MagicMock(side_effect=run_agent)

    cli.input_router.code_audit_workflow._handle_zero_finding_review("audit", scope)

    workspace = cli.runtime.workspace_manager.load()
    assert len(workspace.patch_items) == 1
    assert workspace.patch_items[0].draft.new_string == "zero-fix"
    cli.renderer.print_no_issue_panel.assert_not_called()


def test_review_apply_failure_keeps_current_patch_pending(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.command_handlers.handle_apply = MagicMock(return_value=False)

    cli.input_router.handle_review_input("apply", workspace.patch_items[0])

    updated = cli.runtime.workspace_manager.load()
    assert updated.current_patch_index == 0
    assert updated.patch_items[0].status is PatchReviewStatus.PENDING


def test_cli_wires_llm_intent_classifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)

    cli_obj = AutoPatchCli(tmp_path)

    assert cli_obj.runtime.intent_detector is not None
    assert cli_obj.runtime.intent_detector.classify_with_llm is not None
    assert cli_obj.runtime.intent_detector.classify("@Foo.java check code", False) is IntentType.CODE_AUDIT


def test_handle_chat_routes_llm_code_audit_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        content = "code_audit"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.input_router.handle_code_audit = MagicMock()
    cli_obj.input_router.handle_general_chat = MagicMock()

    cli_obj.input_router.handle_chat("@Foo.java check code")

    cli_obj.input_router.handle_code_audit.assert_called_once_with("@Foo.java check code")
    cli_obj.input_router.handle_general_chat.assert_not_called()


def test_handle_chat_filters_patch_intent_without_pending_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        content = "patch_revise"

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("autopatch_j.cli.app.build_default_llm_client", lambda: FakeLLM())
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.input_router.handle_general_chat = MagicMock()
    cli_obj.input_router.handle_patch_revise = MagicMock()
    cli_obj.input_router.handle_patch_explain = MagicMock()

    cli_obj.input_router.handle_chat("revise this patch")

    cli_obj.input_router.handle_general_chat.assert_called_once_with("revise this patch")
    cli_obj.input_router.handle_patch_revise.assert_not_called()
    cli_obj.input_router.handle_patch_explain.assert_not_called()


def test_handle_chat_switches_new_task_with_pending_review(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route_with_diagnostics.return_value = RouteClassificationResult(
        route=ConversationRoute.NEW_TASK,
        source="test",
    )
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify_with_diagnostics.return_value = IntentClassificationResult(
        intent=IntentType.GENERAL_CHAT,
        source="test",
    )
    cli.input_router.handle_general_chat = MagicMock()

    cli.input_router.handle_chat("@Foo.java explain code")

    assert cli.runtime.workspace_manager.load().has_pending_patch() is False
    cli.renderer.print_agent_text.assert_any_call("已切换到新任务")
    cli.input_router.handle_general_chat.assert_called_once_with("@Foo.java explain code")


def test_handle_code_explain_without_scope_uses_project_context(tmp_path: Path) -> None:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    java_dir = tmp_path / "src" / "main" / "java" / "demo"
    java_dir.mkdir(parents=True)
    (java_dir / "App.java").write_text("class App {}", encoding="utf-8")
    cli_obj = AutoPatchCli(tmp_path)
    cli_obj.renderer = MagicMock()
    cli_obj.command_router = CommandRouter(cli_obj.command_handlers, cli_obj.renderer)
    cli_obj.initialize_runtime(cli_obj.repo_root)
    cli_obj.agent_runner.run = MagicMock(return_value=_presented(display_answer="项目说明"))
    cli_obj.runtime.agent.perform_code_explain = MagicMock(return_value=_agent_result("项目说明"))

    cli_obj.input_router.handle_code_explain("这个项目是干什么的")

    kwargs = cli_obj.agent_runner.run.call_args.kwargs
    assert kwargs["answer_intent"] is IntentType.CODE_EXPLAIN
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli_obj.renderer.print_user_anchor.assert_not_called()
    kwargs["agent_call"]("prompt")
    call_kwargs = cli_obj.runtime.agent.perform_code_explain.call_args.kwargs
    assert call_kwargs["scope"].kind is CodeScopeKind.PROJECT
    assert "项目轻量上下文" in call_kwargs["project_context"]


def test_code_explain_without_focus_files_persists_local_visible_answer(
    cli: AutoPatchCli,
) -> None:
    scope = CodeScope(
        kind=CodeScopeKind.PROJECT,
        source_roots=[],
        focus_files=[],
        is_locked=False,
    )
    turn = SimpleNamespace(id="turn-local", thread_id="thread-local")
    cli.runtime.scope_service.resolve = MagicMock(return_value=scope)
    cli.runtime.memory_manager.begin_turn = MagicMock(return_value=turn)
    cli.runtime.memory_manager.complete_turn = MagicMock()
    cli.agent_runner.run = MagicMock()

    cli.input_router.handle_code_explain("解释这个空项目")

    cli.runtime.memory_manager.begin_turn.assert_called_once_with(
        intent=IntentType.CODE_EXPLAIN,
        user_text="解释这个空项目",
        scope_paths=[],
    )
    cli.runtime.memory_manager.complete_turn.assert_called_once_with(
        "turn-local",
        assistant_text="当前项目缺少可解释的 Java 源码范围。",
    )
    cli.renderer.print_agent_text.assert_called_once_with(
        "当前项目缺少可解释的 Java 源码范围。"
    )
    cli.agent_runner.run.assert_not_called()
    assert cli.runtime.agent.session.memory_thread_id is None


def test_general_chat_does_not_print_chat_anchors(cli: AutoPatchCli) -> None:
    cli.agent_runner.run = MagicMock(return_value=_presented(display_answer="题解"))

    cli.input_router.handle_general_chat("leetcode 第一题题解")

    kwargs = cli.agent_runner.run.call_args.kwargs
    assert kwargs["answer_intent"] is IntentType.GENERAL_CHAT
    assert kwargs["plain_answer"] is True
    assert "show_chat_anchors" not in kwargs
    cli.renderer.print_user_anchor.assert_not_called()


def test_general_chat_persists_only_user_visible_final_answer(cli: AutoPatchCli) -> None:
    turn = SimpleNamespace(id="turn-1", thread_id="thread-1")
    cli.runtime.memory_manager.begin_turn = MagicMock(return_value=turn)
    cli.runtime.memory_manager.complete_turn = MagicMock()
    cli.agent_runner.run = MagicMock(
        return_value=PresentedAgentResult(
            raw_answer="## Raw answer",
            display_answer="Raw answer",
            trace_messages=[{"role": "tool", "content": "not persisted"}],
        )
    )

    cli.input_router.handle_general_chat("请解释 Optional")

    cli.runtime.memory_manager.begin_turn.assert_called_once_with(
        intent=IntentType.GENERAL_CHAT,
        user_text="请解释 Optional",
        scope_paths=[],
    )
    cli.runtime.memory_manager.complete_turn.assert_called_once_with(
        "turn-1",
        assistant_text="Raw answer",
    )
    assert cli.runtime.agent.session.memory_thread_id is None


def test_real_memory_export_excludes_agent_trace_reasoning_and_observations(
    cli: AutoPatchCli,
    tmp_path: Path,
) -> None:
    cli.runtime.memory_manager.close()
    manager = MemoryManager(db_path=tmp_path / "clean-memory.db")
    cli.runtime.memory_manager = manager
    cli.runtime.agent.session.memory_manager = manager
    cli.agent_runner.run = MagicMock(
        return_value=PresentedAgentResult(
            raw_answer="RAW_INTERNAL_SECRET",
            display_answer="用户最终看到的回答",
            trace_messages=[
                {
                    "role": "assistant",
                    "content": "intermediate",
                    "reasoning_content": "REASONING_SECRET",
                },
                {
                    "role": "tool",
                    "name": "memory_search",
                    "content": "TOOL_OBSERVATION_SECRET",
                },
            ],
        )
    )

    try:
        cli.input_router.handle_general_chat("请继续")
        exported = manager.export(tmp_path / "exports")
        payload = json.loads(exported.path.read_text(encoding="utf-8"))
    finally:
        manager.close()

    assert len(payload["turns"]) == 1
    assert payload["turns"][0]["user_text"] == "请继续"
    assert payload["turns"][0]["assistant_text"] == "用户最终看到的回答"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "RAW_INTERNAL_SECRET" not in serialized
    assert "REASONING_SECRET" not in serialized
    assert "TOOL_OBSERVATION_SECRET" not in serialized


def test_general_chat_marks_open_turn_failed_when_request_raises(cli: AutoPatchCli) -> None:
    turn = SimpleNamespace(id="turn-1", thread_id="thread-1")
    cli.runtime.memory_manager.begin_turn = MagicMock(return_value=turn)
    cli.runtime.memory_manager.fail_turn = MagicMock()
    cli.agent_runner.run = MagicMock(side_effect=RuntimeError("model unavailable"))

    with pytest.raises(RuntimeError, match="model unavailable"):
        cli.input_router.handle_general_chat("继续之前的话题")

    cli.runtime.memory_manager.fail_turn.assert_called_once_with(
        "turn-1",
        error="RuntimeError: model unavailable",
    )
    assert cli.runtime.agent.session.memory_thread_id is None


def test_missing_meta_rejects_ordinary_request_before_main_llm(
    cli: AutoPatchCli,
) -> None:
    manager = cli.runtime.memory_manager
    manager.close()
    with sqlite3.connect(manager.db_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        connection.execute("DELETE FROM memory_meta")
    cli.agent_runner.run = MagicMock()

    with pytest.raises(MemorySchemaError, match="memory_meta"):
        cli.input_router.handle_general_chat("不得调用主模型")

    cli.agent_runner.run.assert_not_called()
    assert cli.runtime.agent.session.memory_thread_id is None
    assert manager.status().degraded
    with sqlite3.connect(manager.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == before


def test_ordinary_request_remains_bound_to_old_thread_during_new(
    cli: AutoPatchCli,
) -> None:
    manager = cli.runtime.memory_manager
    manager.close()
    store = manager.store
    old_thread = manager.ensure_active_thread()
    previous = manager.begin_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="old history",
    )
    manager.complete_turn(previous.id, assistant_text="old answer")
    item_id = "memory_old_thread_r1"
    now = store._now()
    with store._transaction() as connection:
        connection.execute(
            """
            INSERT INTO memory_items(
                id, logical_id, revision, kind, thread_id, title, content,
                synopsis, status, non_factual, created_at, updated_at
            ) VALUES (?, 'memory_old_thread', 1, 'discussion_context', ?, ?, ?,
                      ?, 'active', 1, ?, ?)
            """,
            (
                item_id,
                old_thread.id,
                "old thread topic",
                "continue the old discussion",
                "old discussion",
                now,
                now,
            ),
        )
        store._insert_terms(
            connection,
            item_id,
            "old thread topic",
            "continue the old discussion",
            ("old topic",),
            (),
        )
    cli.command_handlers._flush_memory_once = MagicMock()
    session = cli.runtime.agent.session

    def run_old_request(**_kwargs: object) -> PresentedAgentResult:
        assert session.memory_thread_id == old_thread.id
        assert any(
            message["content"] == "old history"
            for message in session.build_thread_history(IntentType.GENERAL_CHAT)
        )
        assert item_id in session.build_memory_context(IntentType.GENERAL_CHAT)

        cli.command_handlers.handle_new()

        assert session.memory_thread_id == old_thread.id
        assert any(
            message["content"] == "old history"
            for message in session.build_thread_history(IntentType.GENERAL_CHAT)
        )
        assert item_id in session.build_memory_context(IntentType.GENERAL_CHAT)
        assert [
            hit.id
            for hit in manager.search(
                "old thread topic", thread_id=session.memory_thread_id
            )
        ] == [item_id]
        assert manager.read(item_id, thread_id=session.memory_thread_id).id == item_id
        return _presented(display_answer="old request answer")

    cli.agent_runner.run = MagicMock(side_effect=run_old_request)
    cli.input_router.handle_general_chat("old request")

    assert session.memory_thread_id is None
    new_thread = manager.ensure_active_thread()
    assert new_thread.id != old_thread.id

    def run_new_request(**_kwargs: object) -> PresentedAgentResult:
        assert session.memory_thread_id == new_thread.id
        assert session.build_thread_history(IntentType.GENERAL_CHAT) == []
        assert item_id not in session.build_memory_context(IntentType.GENERAL_CHAT)
        assert manager.search(
            "old thread topic", thread_id=session.memory_thread_id
        ) == []
        return _presented(display_answer="new request answer")

    cli.agent_runner.run = MagicMock(side_effect=run_new_request)
    cli.input_router.handle_general_chat("new request")

    assert session.memory_thread_id is None


def test_reset_clears_project_state_and_requires_reinit(cli: AutoPatchCli) -> None:
    state_dir = cli.repo_root / ".autopatch-j"
    findings_dir = state_dir / "findings"
    runtime_dir = state_dir / "runtime" / "semgrep"
    findings_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "workspace.json").write_text("{}", encoding="utf-8")
    (state_dir / "history.txt").write_text("/status\n", encoding="utf-8")
    (state_dir / "memory-export-demo.json").write_text("{}", encoding="utf-8")
    (findings_dir / "scan-demo.json").write_text("{}", encoding="utf-8")
    (runtime_dir / "cache.txt").write_text("cache", encoding="utf-8")
    memory_db = state_dir / "memory.db"
    assert memory_db.exists()

    cli.command_handlers.handle_reset()

    assert state_dir.exists()
    assert memory_db.exists()
    assert (state_dir / "memory-export-demo.json").exists()
    assert (state_dir / "history.txt").read_text(encoding="utf-8") == "/status\n"
    assert not findings_dir.exists()
    assert not runtime_dir.exists()
    assert not (state_dir / "workspace.json").exists()
    assert cli.runtime is None
    assert cli.input_router is None
    assert cli.agent_runner is None
    cli.renderer.print_success.assert_called_once_with(
        "项目工作台已重置；Memory、Memory 导出和 CLI history 已保留。"
        "如需清空 Memory，请执行 /memory clear --confirm。"
    )


def test_new_flushes_old_thread_aborts_pending_patch_and_starts_thread(cli: AutoPatchCli) -> None:
    cli.runtime.workspace_manager.save(
        ReviewWorkspace(
            mode=WorkspaceStatus.REVIEWING,
            scope=CodeScope(
                kind=CodeScopeKind.PROJECT,
                source_roots=[],
                focus_files=[],
                is_locked=False,
            ),
            latest_scan_id="scan-1",
            patch_items=[_item("item-1")],
            current_patch_index=0,
        )
    )
    manager = MagicMock()
    manager.ensure_active_thread.return_value = SimpleNamespace(id="thread-old")
    manager.start_new_thread.return_value = SimpleNamespace(id="thread-new")
    manager.flush_once.return_value = SimpleNamespace(failed=0, pending=0)
    cli.runtime.memory_manager = manager
    cli.runtime.agent.reset_history = MagicMock()

    cli.command_handlers.handle_new()

    manager.flush_once.assert_called_once_with(reason="new", thread_id="thread-old")
    manager.start_new_thread.assert_called_once_with(expected_thread_id="thread-old")
    assert cli.runtime.workspace_manager.load().has_pending_patch() is False
    cli.runtime.agent.reset_history.assert_called_once_with()
    cli.renderer.print_agent_text.assert_any_call("已中止待确认补丁并清空 review workspace。")
    cli.renderer.print_success.assert_called_once_with("已创建新的普通对话 thread：thread-new")


def test_memory_clear_requires_confirmation(cli: AutoPatchCli) -> None:
    cli.runtime.memory_manager.clear = MagicMock()

    cli.command_handlers.handle_memory(["clear"])

    cli.runtime.memory_manager.clear.assert_not_called()
    assert "--confirm" in cli.renderer.print_error.call_args.args[0]


def test_memory_forget_reports_that_raw_turns_are_retained(cli: AutoPatchCli) -> None:
    cli.runtime.memory_manager.forget = MagicMock(
        return_value=SimpleNamespace(memory_id="memory-1", forgotten=True, raw_turns_retained=True)
    )

    cli.command_handlers.handle_memory(["forget", "memory-1"])

    cli.runtime.memory_manager.forget.assert_called_once_with("memory-1")
    assert "原始 turn 仍被保留" in cli.renderer.print_success.call_args.args[0]


def test_memory_export_reports_raw_snapshot_path(cli: AutoPatchCli, tmp_path: Path) -> None:
    export_path = tmp_path / ".autopatch-j" / "memory-export-test.json"
    cli.runtime.memory_manager.export = MagicMock(return_value=SimpleNamespace(path=export_path))

    cli.command_handlers.handle_memory(["export"])

    cli.runtime.memory_manager.export.assert_called_once_with()
    message = cli.renderer.print_success.call_args.args[0]
    assert str(export_path) in message
    assert "未脱敏" in message


def _memory_status_with_error(db_path: Path, error: str) -> MemoryStatus:
    return MemoryStatus(
        healthy=True,
        degraded=False,
        db_path=db_path,
        schema_version=2,
        generation=1,
        active_thread_id="thread-active",
        thread_count=1,
        turn_count=1,
        active_item_count=0,
        pending_jobs=0,
        leased_jobs=0,
        retry_wait_jobs=1,
        last_error=error,
        last_succeeded_at=None,
    )


def test_memory_status_hides_raw_error_outside_debug_mode(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_error = "RAW_PROVIDER_SENTINEL"
    monkeypatch.setattr(GlobalConfig, "debug_mode", False)
    cli.runtime.memory_manager.status = MagicMock(
        return_value=_memory_status_with_error(cli.runtime.memory_manager.db_path, raw_error)
    )

    cli.command_handlers.handle_memory(["status"])

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]
    assert raw_error not in cells
    assert "已记录（启用 AUTOPATCH_DEBUG=true 查看 RAW 错误）" in cells


def test_memory_status_shows_raw_error_in_debug_mode(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_error = "RAW_PROVIDER_SENTINEL"
    monkeypatch.setattr(GlobalConfig, "debug_mode", True)
    cli.runtime.memory_manager.status = MagicMock(
        return_value=_memory_status_with_error(cli.runtime.memory_manager.db_path, raw_error)
    )

    cli.command_handlers.handle_memory(["status"])

    table = cli.renderer.print_panel.call_args.args[0]
    cells = [str(cell) for column in table.columns for cell in column._cells]
    assert raw_error in cells
    assert "已记录（启用 AUTOPATCH_DEBUG=true 查看 RAW 错误）" not in cells


def test_runtime_error_hides_raw_provider_failure_outside_debug_mode(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GlobalConfig, "debug_mode", False)

    cli._render_runtime_error(RuntimeError("RAW_PROVIDER_SENTINEL"))

    message = cli.renderer.print_error.call_args.args[0]
    assert "RAW_PROVIDER_SENTINEL" not in message
    assert "AUTOPATCH_DEBUG=true" in message


def test_runtime_error_shows_raw_provider_failure_in_debug_mode(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderError(RuntimeError):
        status_code = 503
        body = {"detail": "RAW_PROVIDER_SENTINEL"}

    monkeypatch.setattr(GlobalConfig, "debug_mode", True)

    cli._render_runtime_error(ProviderError("provider unavailable"))

    message = cli.renderer.print_error.call_args.args[0]
    assert "ProviderError: provider unavailable" in message
    assert "status_code: 503" in message
    assert 'body: {"detail": "RAW_PROVIDER_SENTINEL"}' in message


def test_cli_exit_flushes_memory_once_and_closes_idempotently(cli: AutoPatchCli) -> None:
    flush_once = MagicMock(
        return_value=SimpleNamespace(processed=1, succeeded=1, failed=0, pending=0)
    )
    cli.runtime.memory_manager.flush_once = flush_once
    close = MagicMock()
    cli.runtime.memory_manager.close = close

    cli._finalize_cli_exit()
    cli._finalize_cli_exit()

    flush_once.assert_called_once_with(reason="exit", thread_id=None)
    close.assert_called_once_with()


def test_cli_exit_warns_but_still_closes_when_flush_fails(cli: AutoPatchCli) -> None:
    cli.runtime.memory_manager.flush_once = MagicMock(side_effect=RuntimeError("timeout"))
    close = MagicMock()
    cli.runtime.memory_manager.close = close

    cli._finalize_cli_exit()

    assert "下次启动恢复" in cli.renderer.print_error.call_args.args[0]
    close.assert_called_once_with()


def test_handle_chat_maps_pending_code_explain_without_scope_to_patch_explain(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route_with_diagnostics.return_value = RouteClassificationResult(
        route=ConversationRoute.REVIEW_CONTINUE,
        source="test",
    )
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify_with_diagnostics.return_value = IntentClassificationResult(
        intent=IntentType.CODE_EXPLAIN,
        source="test",
    )
    cli.input_router.handle_patch_explain = MagicMock()
    cli.input_router.handle_code_explain = MagicMock()

    cli.input_router.handle_chat("解释一下")

    cli.input_router.handle_patch_explain.assert_called_once_with("解释一下")
    cli.input_router.handle_code_explain.assert_not_called()


def test_handle_chat_keeps_pending_patch_revise_route(cli: AutoPatchCli) -> None:
    workspace = ReviewWorkspace(
        mode=WorkspaceStatus.REVIEWING,
        scope=CodeScope(
            kind=CodeScopeKind.PROJECT,
            source_roots=[],
            focus_files=[],
            is_locked=False,
        ),
        latest_scan_id="scan-1",
        patch_items=[_item("item-1")],
        current_patch_index=0,
    )
    cli.runtime.workspace_manager.save(workspace)
    cli.runtime.conversation_router = MagicMock()
    cli.runtime.conversation_router.classify_route_with_diagnostics.return_value = RouteClassificationResult(
        route=ConversationRoute.REVIEW_CONTINUE,
        source="test",
    )
    cli.runtime.intent_detector = MagicMock()
    cli.runtime.intent_detector.classify_with_diagnostics.return_value = IntentClassificationResult(
        intent=IntentType.PATCH_REVISE,
        source="test",
    )
    cli.input_router.handle_patch_revise = MagicMock()

    cli.input_router.handle_chat("重新写这个补丁")

    cli.input_router.handle_patch_revise.assert_called_once_with("重新写这个补丁")


def test_debug_mode_renders_classifier_fallback_diagnostics(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GlobalConfig, "debug_mode", True)

    cli.input_router._render_route_diagnostic(
        RouteClassificationResult(
            route=ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason="classifier timeout",
        )
    )
    cli.input_router._render_intent_diagnostic(
        IntentClassificationResult(
            intent=IntentType.GENERAL_CHAT,
            source="fallback",
            fallback_reason="invalid label",
        )
    )

    cli.renderer.print_agent_text.assert_any_call(
        "路由诊断：route 使用 fallback review_continue，原因：classifier timeout"
    )
    cli.renderer.print_agent_text.assert_any_call(
        "路由诊断：intent 使用 fallback general_chat，原因：invalid label"
    )


def test_normal_mode_hides_classifier_fallback_diagnostics(
    cli: AutoPatchCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GlobalConfig, "debug_mode", False)
    cli.renderer.print_agent_text.reset_mock()

    cli.input_router._render_route_diagnostic(
        RouteClassificationResult(
            route=ConversationRoute.REVIEW_CONTINUE,
            source="fallback",
            fallback_reason="RAW_ROUTE_SENTINEL",
        )
    )
    cli.input_router._render_intent_diagnostic(
        IntentClassificationResult(
            intent=IntentType.GENERAL_CHAT,
            source="fallback",
            fallback_reason="RAW_INTENT_SENTINEL",
        )
    )

    cli.renderer.print_agent_text.assert_not_called()
